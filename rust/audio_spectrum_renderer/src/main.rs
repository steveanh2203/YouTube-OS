use rustfft::{num_complex::Complex32, FftPlanner};
use serde::Deserialize;
use std::f32::consts::PI;
use std::io::{self, Read, Write};
use std::process::Command;

const DEFAULT_SAMPLE_RATE: usize = 44_100;
const DEFAULT_FFT_SIZE: usize = 2048;
const DEFAULT_MIN_DECIBELS: f32 = -92.0;
const DEFAULT_MAX_DECIBELS: f32 = -18.0;
const SPECTRUM_FREQ_ATTACK: f32 = 0.64;
const SPECTRUM_FREQ_RELEASE: f32 = 0.34;
const SPECTRUM_MONSTERCAT_AMOUNT: f32 = 2.55;
const SPECTRUM_BAR_ATTACK: f32 = 0.84;
const SPECTRUM_BAR_RELEASE: f32 = 0.22;
const SPECTRUM_BAR_SHAPE_BLEND: f32 = 0.14;
const SPECTRUM_PEAK_RISE: f32 = 0.02;
const SPECTRUM_PEAK_DECAY: f32 = 0.03;

#[derive(Debug, Deserialize)]
struct RenderInput {
    audio_path: String,
    fps: usize,
    color_hex: String,
    overlay_width: usize,
    overlay_height: usize,
    bins: usize,
    bar_width: usize,
    pitch: usize,
    min_height: usize,
    max_height: usize,
    top_base_y: usize,
    bottom_base_y: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RasterBackend {
    Cpu,
    Metal,
}

#[derive(Clone, Copy)]
struct SpectrumColors {
    base_rgb: (u8, u8, u8),
    bar_rgb: (u8, u8, u8),
    tip_rgb: (u8, u8, u8),
    line_rgb: (u8, u8, u8),
}

fn resolve_raster_backend() -> RasterBackend {
    match std::env::var("AUTOCAPCUT_AUDIO_VIS_RUST_BACKEND")
        .unwrap_or_else(|_| "auto".to_string())
        .trim()
        .to_lowercase()
        .as_str()
    {
        "cpu" => RasterBackend::Cpu,
        "metal" => RasterBackend::Metal,
        _ => {
            #[cfg(target_os = "macos")]
            {
                RasterBackend::Metal
            }
            #[cfg(not(target_os = "macos"))]
            {
                RasterBackend::Cpu
            }
        }
    }
}

fn clamp01(value: f32) -> f32 {
    value.clamp(0.0, 1.0)
}

fn brighten_rgb(color: (u8, u8, u8), amount: f32) -> (u8, u8, u8) {
    (
        (color.0 as f32 + (255.0 - color.0 as f32) * amount).round().clamp(0.0, 255.0) as u8,
        (color.1 as f32 + (255.0 - color.1 as f32) * amount).round().clamp(0.0, 255.0) as u8,
        (color.2 as f32 + (255.0 - color.2 as f32) * amount).round().clamp(0.0, 255.0) as u8,
    )
}

fn with_alpha_over_black(color: (u8, u8, u8), alpha: u8) -> (u8, u8, u8) {
    let factor = alpha as f32 / 255.0;
    (
        (color.0 as f32 * factor).round().clamp(0.0, 255.0) as u8,
        (color.1 as f32 * factor).round().clamp(0.0, 255.0) as u8,
        (color.2 as f32 * factor).round().clamp(0.0, 255.0) as u8,
    )
}

fn hex_to_rgb(color_hex: &str) -> Result<(u8, u8, u8), String> {
    let value = color_hex.trim().trim_start_matches('#');
    if value.len() != 6 {
        return Err(format!("invalid color hex: {color_hex}"));
    }
    let r = u8::from_str_radix(&value[0..2], 16).map_err(|_| format!("invalid color hex: {color_hex}"))?;
    let g = u8::from_str_radix(&value[2..4], 16).map_err(|_| format!("invalid color hex: {color_hex}"))?;
    let b = u8::from_str_radix(&value[4..6], 16).map_err(|_| format!("invalid color hex: {color_hex}"))?;
    Ok((r, g, b))
}

fn build_colors(color_hex: &str) -> Result<SpectrumColors, String> {
    let vis_rgb = hex_to_rgb(color_hex)?;
    Ok(SpectrumColors {
        tip_rgb: brighten_rgb(vis_rgb, 0.32),
        base_rgb: with_alpha_over_black(vis_rgb, 0x50),
        bar_rgb: with_alpha_over_black(vis_rgb, 0xD8),
        line_rgb: with_alpha_over_black(brighten_rgb(vis_rgb, 0.18), 0x66),
    })
}

fn analysis_window() -> Vec<f32> {
    (0..DEFAULT_FFT_SIZE)
        .map(|idx| 0.5 - 0.5 * ((2.0 * PI * idx as f32) / (DEFAULT_FFT_SIZE.saturating_sub(1) as f32)).cos())
        .collect()
}

fn decode_audio_mono(audio_path: &str) -> Result<Vec<f32>, String> {
    let output = Command::new("ffmpeg")
        .args([
            "-v",
            "error",
            "-i",
            audio_path,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-f",
            "f32le",
            "pipe:1",
        ])
        .output()
        .map_err(|err| format!("failed to start ffmpeg: {err}"))?;

    if !output.status.success() {
        return Err(format!(
            "ffmpeg decode failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    let mut samples = Vec::with_capacity(output.stdout.len() / 4);
    for chunk in output.stdout.chunks_exact(4) {
        samples.push(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]));
    }
    Ok(samples)
}

fn blur_bins(values: &[f32]) -> Vec<f32> {
    let count = values.len();
    let mut out = vec![0.0; count];
    for i in 0..count {
        let left2 = values[i.saturating_sub(2)];
        let left1 = values[i.saturating_sub(1)];
        let center = values[i];
        let right1 = values[(i + 1).min(count.saturating_sub(1))];
        let right2 = values[(i + 2).min(count.saturating_sub(1))];
        out[i] = left2 * 0.02 + left1 * 0.12 + center * 0.72 + right1 * 0.12 + right2 * 0.02;
    }
    out
}

fn apply_monstercat(values: &[f32], amount: f32) -> Vec<f32> {
    let mut out = values.to_vec();
    let count = out.len();
    let falloff = amount * 1.5;
    for z in 0..count {
        for y in (0..z).rev() {
            let distance = (z - y) as i32;
            let candidate = out[z] / falloff.powi(distance);
            if candidate > out[y] {
                out[y] = candidate;
            }
        }
        for y in (z + 1)..count {
            let distance = (y - z) as i32;
            let candidate = out[z] / falloff.powi(distance);
            if candidate > out[y] {
                out[y] = candidate;
            }
        }
    }
    out
}

fn sample_spectrum_bins(source: &[f32], bins: usize) -> Vec<f32> {
    let mut out = vec![0.0; bins];
    let max_index = source.len().saturating_sub(1);
    let mut frame_peak: f32 = 0.0;

    for i in 0..bins {
        let start_t = i as f32 / bins.saturating_sub(1).max(1) as f32;
        let end_t = (i + 1) as f32 / bins.max(1) as f32;
        let map_start = start_t * 0.58 + start_t.powf(1.12) * 0.42;
        let map_end = end_t * 0.58 + end_t.powf(1.12) * 0.42;
        let start_idx = (map_start * max_index as f32).floor() as usize;
        let end_idx = ((map_end * max_index as f32).floor() as usize).max(start_idx + 1);

        let mut weighted_sum: f32 = 0.0;
        let mut total_weight: f32 = 0.0;
        let mut slice_peak: f32 = 0.0;
        let upper = end_idx.min(max_index);
        for idx in start_idx..=upper {
            let position = if max_index == 0 { 0.0 } else { idx as f32 / max_index as f32 };
            let psycho_weight = 0.9 + position * 1.45;
            let weighted = source[idx] * psycho_weight;
            weighted_sum += weighted;
            total_weight += psycho_weight;
            slice_peak = slice_peak.max(weighted);
        }

        let avg = if total_weight > 0.0 { weighted_sum / total_weight } else { 0.0 };
        let blended = avg * 0.45 + slice_peak * 0.55;
        let edge_compensation = 0.96 + (i as f32 / bins.saturating_sub(1).max(1) as f32).powf(1.18) * 1.15;
        let compensated = blended * edge_compensation;
        out[i] = compensated;
        frame_peak = frame_peak.max(compensated);
    }

    if frame_peak <= 0.0 {
        return out;
    }

    let envelope = blur_bins(&out);
    let floor = frame_peak * 0.05;
    let mut normalized = vec![0.0; bins];
    let mut avg_lifted = 0.0;
    for i in 0..bins {
        let lifted = clamp01((out[i] - floor) / (frame_peak - floor).max(1e-5));
        normalized[i] = lifted;
        avg_lifted += lifted;
    }
    avg_lifted /= bins.max(1) as f32;

    for i in 0..bins {
        let lifted = normalized[i];
        let local_envelope = envelope[i].max(frame_peak * 0.14);
        let local_contrast = clamp01(out[i] / local_envelope);
        let position = i as f32 / bins.saturating_sub(1).max(1) as f32;
        let distance_from_center = (position - 0.5).abs() / 0.5;
        let center_focus = 1.0 - clamp01(distance_from_center).powf(1.25);
        let belly_envelope = 0.18 + center_focus * 0.92;
        let shared_motion = avg_lifted * (0.2 + center_focus * 0.55);
        let edge_drop = 0.02 + center_focus * 0.06;
        let center_lift = center_focus * (0.12 + avg_lifted * 0.28);
        let local_detail = local_contrast * (0.05 + center_focus * 0.16);
        let spread = lifted * (0.26 + center_focus * 0.34) + shared_motion * 0.28 + local_detail;
        let rhythmic_floor = shared_motion * (0.54 + center_focus * 0.26) + center_lift;
        out[i] = clamp01((spread.max(rhythmic_floor) + edge_drop) * belly_envelope).powf(0.98);
    }

    out
}

fn spectrum_current(samples: &[f32], frame_idx: usize, fps: usize, window: &[f32], fft: &dyn rustfft::Fft<f32>) -> Vec<f32> {
    let hop = DEFAULT_SAMPLE_RATE as f32 / fps.max(1) as f32;
    let end = (((frame_idx as f32) + 1.0) * hop) as isize;
    let mut buffer = vec![Complex32::new(0.0, 0.0); DEFAULT_FFT_SIZE];

    for i in 0..DEFAULT_FFT_SIZE {
        let sample_idx = end - DEFAULT_FFT_SIZE as isize + i as isize;
        let sample = if sample_idx >= 0 && (sample_idx as usize) < samples.len() {
            samples[sample_idx as usize]
        } else {
            0.0
        };
        buffer[i] = Complex32::new(sample * window[i], 0.0);
    }

    fft.process(&mut buffer);

    let freq_count = DEFAULT_FFT_SIZE / 2 + 1;
    let mut out = vec![0.0; freq_count];
    for i in 0..freq_count {
        let mag = buffer[i].norm();
        let db = 20.0 * (mag + 1e-6).log10();
        out[i] = clamp01((db - DEFAULT_MIN_DECIBELS) / (DEFAULT_MAX_DECIBELS - DEFAULT_MIN_DECIBELS)) * 255.0;
    }
    out
}

fn put_pixel(frame: &mut [u8], width: usize, height: usize, x: usize, y: usize, color: (u8, u8, u8)) {
    if x >= width || y >= height {
        return;
    }
    let idx = (y * width + x) * 3;
    frame[idx] = color.0;
    frame[idx + 1] = color.1;
    frame[idx + 2] = color.2;
}

fn fill_rect(frame: &mut [u8], width: usize, height: usize, x: usize, y: usize, rect_w: usize, rect_h: usize, color: (u8, u8, u8)) {
    let x2 = (x + rect_w).min(width);
    let y2 = (y + rect_h).min(height);
    for yy in y..y2 {
        for xx in x..x2 {
            put_pixel(frame, width, height, xx, yy, color);
        }
    }
}

fn draw_gradient_rect(
    frame: &mut [u8],
    width: usize,
    height: usize,
    x: usize,
    y: usize,
    rect_w: usize,
    rect_h: usize,
    top: (u8, u8, u8),
    bottom: (u8, u8, u8),
) {
    if rect_h == 0 {
        return;
    }
    let x2 = (x + rect_w).min(width);
    let y2 = (y + rect_h).min(height);
    let effective_h = y2.saturating_sub(y).max(1);
    for yy in y..y2 {
        let t = if effective_h <= 1 { 0.0 } else { (yy - y) as f32 / (effective_h - 1) as f32 };
        let color = (
            (top.0 as f32 + (bottom.0 as f32 - top.0 as f32) * t).round().clamp(0.0, 255.0) as u8,
            (top.1 as f32 + (bottom.1 as f32 - top.1 as f32) * t).round().clamp(0.0, 255.0) as u8,
            (top.2 as f32 + (bottom.2 as f32 - top.2 as f32) * t).round().clamp(0.0, 255.0) as u8,
        );
        for xx in x..x2 {
            put_pixel(frame, width, height, xx, yy, color);
        }
    }
}

fn render_frame_cpu(input: &RenderInput, colors: SpectrumColors, bars: &[f32], peaks: &[f32]) -> Vec<u8> {
    let mut frame = vec![0u8; input.overlay_width * input.overlay_height * 3];
    fill_rect(&mut frame, input.overlay_width, input.overlay_height, 0, input.top_base_y, input.overlay_width, 1, colors.line_rgb);
    fill_rect(&mut frame, input.overlay_width, input.overlay_height, 0, input.bottom_base_y, input.overlay_width, 1, colors.line_rgb);

    for i in 0..input.bins {
        let eased = clamp01(bars[i]).powf(0.92);
        let peak = clamp01(peaks[i]).powf(0.9);
        let bar_height = (input.min_height as f32 + eased * input.max_height as f32).round() as usize;
        let peak_height = (input.min_height as f32 + peak * input.max_height as f32).round() as usize;
        let x = i * input.pitch;

        fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.top_base_y.saturating_sub(input.min_height), input.bar_width, input.min_height, colors.base_rgb);
        fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.bottom_base_y, input.bar_width, input.min_height, colors.base_rgb);

        if bar_height > input.min_height + 2 {
            let body_height = bar_height - input.min_height + 1;
            draw_gradient_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.top_base_y.saturating_sub(bar_height), input.bar_width, body_height, colors.tip_rgb, colors.bar_rgb);
            draw_gradient_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.bottom_base_y + input.min_height.saturating_sub(1), input.bar_width, body_height, colors.bar_rgb, colors.tip_rgb);
            fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.top_base_y.saturating_sub(bar_height), input.bar_width, 2, colors.tip_rgb);
            fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.bottom_base_y + bar_height.saturating_sub(2), input.bar_width, 2, colors.tip_rgb);
        }

        if peak_height > bar_height + 4 {
            fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.top_base_y.saturating_sub(peak_height), input.bar_width, 2, colors.tip_rgb);
            fill_rect(&mut frame, input.overlay_width, input.overlay_height, x, input.bottom_base_y + peak_height.saturating_sub(2), input.bar_width, 2, colors.tip_rgb);
        }
    }

    frame
}

#[cfg(target_os = "macos")]
mod metal_backend {
    use super::{RenderInput, SpectrumColors};
    use metal::{CompileOptions, ComputePipelineState, Device, MTLResourceOptions, MTLSize};
    use std::ffi::c_void;
    use std::mem::size_of;
    use std::ptr;
    use std::slice;

    const SHADER: &str = r#"
#include <metal_stdlib>
using namespace metal;
struct Params {
    uint width;
    uint height;
    uint bins;
    uint bar_width;
    uint pitch;
    uint min_height;
    uint max_height;
    uint top_base_y;
    uint bottom_base_y;
    uint pad0;
    uint pad1;
    uint pad2;
    uint4 base_rgb;
    uint4 bar_rgb;
    uint4 tip_rgb;
    uint4 line_rgb;
};

inline uchar3 pick_color(uint4 c) {
    return uchar3((uchar)c.x, (uchar)c.y, (uchar)c.z);
}

inline uchar3 mix3(uint4 a, uint4 b, float t) {
    float clamped = clamp(t, 0.0f, 1.0f);
    return uchar3(
        (uchar)round(mix((float)a.x, (float)b.x, clamped)),
        (uchar)round(mix((float)a.y, (float)b.y, clamped)),
        (uchar)round(mix((float)a.z, (float)b.z, clamped))
    );
}

kernel void render_spectrum(device uchar *out [[buffer(0)]],
                            device const float *bars [[buffer(1)]],
                            device const float *peaks [[buffer(2)]],
                            constant Params& p [[buffer(3)]],
                            uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.width || gid.y >= p.height) {
        return;
    }

    uchar3 color = uchar3(0, 0, 0);
    bool has_color = false;

    if (gid.y == p.top_base_y || gid.y == p.bottom_base_y) {
        color = pick_color(p.line_rgb);
        has_color = true;
    }

    uint bin = gid.x / p.pitch;
    uint local_x = gid.x - bin * p.pitch;
    if (bin < p.bins && local_x < p.bar_width) {
        float eased = pow(clamp(bars[bin], 0.0f, 1.0f), 0.92f);
        float peak = pow(clamp(peaks[bin], 0.0f, 1.0f), 0.9f);
        uint bar_height = (uint)round((float)p.min_height + eased * (float)p.max_height);
        uint peak_height = (uint)round((float)p.min_height + peak * (float)p.max_height);

        if (gid.y >= p.top_base_y - p.min_height && gid.y < p.top_base_y) {
            color = pick_color(p.base_rgb);
            has_color = true;
        }
        if (gid.y >= p.bottom_base_y && gid.y < p.bottom_base_y + p.min_height) {
            color = pick_color(p.base_rgb);
            has_color = true;
        }

        if (bar_height > p.min_height + 2) {
            uint body_height = bar_height - p.min_height + 1;
            uint top_start = p.top_base_y - bar_height;
            uint bottom_start = p.bottom_base_y + p.min_height - 1;

            if (gid.y >= top_start && gid.y < top_start + body_height) {
                float t = body_height <= 1 ? 0.0f : float(gid.y - top_start) / float(body_height - 1);
                color = mix3(p.tip_rgb, p.bar_rgb, t);
                has_color = true;
            }
            if (gid.y >= bottom_start && gid.y < bottom_start + body_height) {
                float t = body_height <= 1 ? 0.0f : float(gid.y - bottom_start) / float(body_height - 1);
                color = mix3(p.bar_rgb, p.tip_rgb, t);
                has_color = true;
            }
            if (gid.y >= top_start && gid.y < top_start + 2) {
                color = pick_color(p.tip_rgb);
                has_color = true;
            }
            if (gid.y + 2 >= p.bottom_base_y + bar_height && gid.y < p.bottom_base_y + bar_height) {
                color = pick_color(p.tip_rgb);
                has_color = true;
            }
        }

        if (peak_height > bar_height + 4) {
            uint peak_top = p.top_base_y - peak_height;
            uint peak_bottom = p.bottom_base_y + peak_height - 2;
            if (gid.y >= peak_top && gid.y < peak_top + 2) {
                color = pick_color(p.tip_rgb);
                has_color = true;
            }
            if (gid.y >= peak_bottom && gid.y < peak_bottom + 2) {
                color = pick_color(p.tip_rgb);
                has_color = true;
            }
        }
    }

    if (has_color) {
        uint idx = (gid.y * p.width + gid.x) * 3;
        out[idx] = color.x;
        out[idx + 1] = color.y;
        out[idx + 2] = color.z;
    }
}
"#;

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Params {
        width: u32,
        height: u32,
        bins: u32,
        bar_width: u32,
        pitch: u32,
        min_height: u32,
        max_height: u32,
        top_base_y: u32,
        bottom_base_y: u32,
        pad0: u32,
        pad1: u32,
        pad2: u32,
        base_rgb: [u32; 4],
        bar_rgb: [u32; 4],
        tip_rgb: [u32; 4],
        line_rgb: [u32; 4],
    }

    pub struct MetalRenderer {
        queue: metal::CommandQueue,
        pipeline: ComputePipelineState,
        output: metal::Buffer,
        bars_buffer: metal::Buffer,
        peaks_buffer: metal::Buffer,
        params_buffer: metal::Buffer,
        threads_per_group: MTLSize,
        threads_per_grid: MTLSize,
        out_len: usize,
        bins: usize,
    }

    impl MetalRenderer {
        pub fn new(input: &RenderInput, colors: SpectrumColors) -> Result<Self, String> {
            let device = Device::system_default().ok_or_else(|| "Metal device unavailable".to_string())?;
            let options = CompileOptions::new();
            options.set_fast_math_enabled(true);
            let library = device
                .new_library_with_source(SHADER, &options)
                .map_err(|err| format!("metal compile failed: {err}"))?;
            let function = library
                .get_function("render_spectrum", None)
                .map_err(|err| format!("metal function failed: {err}"))?;
            let pipeline = device
                .new_compute_pipeline_state_with_function(&function)
                .map_err(|err| format!("metal pipeline failed: {err}"))?;
            let queue = device.new_command_queue();

            let resource_options = MTLResourceOptions::StorageModeShared;
            let out_len = input.overlay_width * input.overlay_height * 3;
            let output = device.new_buffer(out_len as u64, resource_options);
            let bars_buffer = device.new_buffer((input.bins * size_of::<f32>()) as u64, resource_options);
            let peaks_buffer = device.new_buffer((input.bins * size_of::<f32>()) as u64, resource_options);
            let params = Params {
                width: input.overlay_width as u32,
                height: input.overlay_height as u32,
                bins: input.bins as u32,
                bar_width: input.bar_width as u32,
                pitch: input.pitch as u32,
                min_height: input.min_height as u32,
                max_height: input.max_height as u32,
                top_base_y: input.top_base_y as u32,
                bottom_base_y: input.bottom_base_y as u32,
                pad0: 0,
                pad1: 0,
                pad2: 0,
                base_rgb: [colors.base_rgb.0 as u32, colors.base_rgb.1 as u32, colors.base_rgb.2 as u32, 255],
                bar_rgb: [colors.bar_rgb.0 as u32, colors.bar_rgb.1 as u32, colors.bar_rgb.2 as u32, 255],
                tip_rgb: [colors.tip_rgb.0 as u32, colors.tip_rgb.1 as u32, colors.tip_rgb.2 as u32, 255],
                line_rgb: [colors.line_rgb.0 as u32, colors.line_rgb.1 as u32, colors.line_rgb.2 as u32, 255],
            };
            let params_buffer = device.new_buffer_with_data(
                &params as *const Params as *const c_void,
                size_of::<Params>() as u64,
                resource_options,
            );

            let thread_width = pipeline.thread_execution_width().max(1);
            let max_threads = pipeline.max_total_threads_per_threadgroup().max(thread_width);
            let thread_height = (max_threads / thread_width).max(1).min(8);

            Ok(Self {
                queue,
                pipeline,
                output,
                bars_buffer,
                peaks_buffer,
                params_buffer,
                threads_per_group: MTLSize::new(thread_width, thread_height, 1),
                threads_per_grid: MTLSize::new(input.overlay_width as u64, input.overlay_height as u64, 1),
                out_len,
                bins: input.bins,
            })
        }

        pub fn render(&mut self, bars: &[f32], peaks: &[f32]) -> Result<Vec<u8>, String> {
            if bars.len() != self.bins || peaks.len() != self.bins {
                return Err("invalid metal buffer size".to_string());
            }

            unsafe {
                ptr::copy_nonoverlapping(
                    bars.as_ptr() as *const u8,
                    self.bars_buffer.contents() as *mut u8,
                    self.bins * size_of::<f32>(),
                );
                ptr::copy_nonoverlapping(
                    peaks.as_ptr() as *const u8,
                    self.peaks_buffer.contents() as *mut u8,
                    self.bins * size_of::<f32>(),
                );
                ptr::write_bytes(self.output.contents(), 0, self.out_len);
            }

            let command_buffer = self.queue.new_command_buffer();
            let encoder = command_buffer.new_compute_command_encoder();
            encoder.set_compute_pipeline_state(&self.pipeline);
            encoder.set_buffer(0, Some(&self.output), 0);
            encoder.set_buffer(1, Some(&self.bars_buffer), 0);
            encoder.set_buffer(2, Some(&self.peaks_buffer), 0);
            encoder.set_buffer(3, Some(&self.params_buffer), 0);
            encoder.dispatch_threads(self.threads_per_grid, self.threads_per_group);
            encoder.end_encoding();
            command_buffer.commit();
            command_buffer.wait_until_completed();

            let bytes = unsafe { slice::from_raw_parts(self.output.contents() as *const u8, self.out_len) };
            Ok(bytes.to_vec())
        }
    }
}

enum FrameRenderer {
    Cpu { colors: SpectrumColors },
    #[cfg(target_os = "macos")]
    Metal(metal_backend::MetalRenderer),
}

impl FrameRenderer {
    fn new(input: &RenderInput, colors: SpectrumColors, backend: RasterBackend) -> Result<Self, String> {
        match backend {
            RasterBackend::Cpu => Ok(Self::Cpu { colors }),
            RasterBackend::Metal => {
                #[cfg(target_os = "macos")]
                {
                    Ok(Self::Metal(metal_backend::MetalRenderer::new(input, colors)?))
                }
                #[cfg(not(target_os = "macos"))]
                {
                    let _ = input;
                    let _ = colors;
                    Ok(Self::Cpu { colors })
                }
            }
        }
    }

    fn backend(&self) -> RasterBackend {
        match self {
            Self::Cpu { .. } => RasterBackend::Cpu,
            #[cfg(target_os = "macos")]
            Self::Metal(_) => RasterBackend::Metal,
        }
    }

    fn render(&mut self, input: &RenderInput, bars: &[f32], peaks: &[f32]) -> Result<Vec<u8>, String> {
        match self {
            Self::Cpu { colors } => Ok(render_frame_cpu(input, *colors, bars, peaks)),
            #[cfg(target_os = "macos")]
            Self::Metal(renderer) => renderer.render(bars, peaks),
        }
    }
}

fn main() -> Result<(), String> {
    let mut input_json = String::new();
    io::stdin().read_to_string(&mut input_json).map_err(|err| format!("failed to read stdin: {err}"))?;
    let input: RenderInput = serde_json::from_str(&input_json).map_err(|err| format!("invalid input json: {err}"))?;

    let colors = build_colors(&input.color_hex)?;
    let requested_backend = resolve_raster_backend();
    let mut renderer = match FrameRenderer::new(&input, colors, requested_backend) {
        Ok(renderer) => renderer,
        Err(err) => {
            if requested_backend == RasterBackend::Metal {
                return Err(err);
            }
            eprintln!("metal_fallback={err}");
            FrameRenderer::new(&input, colors, RasterBackend::Cpu)?
        }
    };

    let samples = decode_audio_mono(&input.audio_path)?;
    let total_frames = ((samples.len() as f32 / DEFAULT_SAMPLE_RATE as f32) * input.fps.max(1) as f32).ceil() as usize;
    let total_frames = total_frames.max(1);

    let window = analysis_window();
    let mut planner = FftPlanner::<f32>::new();
    let fft = planner.plan_fft_forward(DEFAULT_FFT_SIZE);
    let mut smooth_freq = vec![0.0; DEFAULT_FFT_SIZE / 2 + 1];
    let mut bars = vec![0.0; input.bins];
    let mut peaks = vec![0.0; input.bins];
    let mut scratch = vec![0.0; input.bins];
    let mut stdout = io::stdout().lock();

    for frame_idx in 0..total_frames {
        let current = spectrum_current(&samples, frame_idx, input.fps, &window, fft.as_ref());
        if frame_idx == 0 {
            smooth_freq.copy_from_slice(&current);
        } else {
            for i in 0..smooth_freq.len() {
                let prev = smooth_freq[i];
                let now = current[i];
                let factor = if now > prev { SPECTRUM_FREQ_ATTACK } else { SPECTRUM_FREQ_RELEASE };
                smooth_freq[i] = prev + (now - prev) * factor;
            }
        }

        let sampled = sample_spectrum_bins(&smooth_freq, input.bins);
        let energized = apply_monstercat(&sampled, SPECTRUM_MONSTERCAT_AMOUNT);
        let blurred = blur_bins(&energized);

        for i in 0..input.bins {
            let prev = bars[i];
            let prev_sample = sampled[i.saturating_sub(1)];
            let next_sample = sampled[(i + 1).min(input.bins.saturating_sub(1))];
            let local_delta = sampled[i] - ((prev_sample + next_sample) * 0.5);
            let micro_detail = clamp01(sampled[i] + local_delta * 0.55);
            let left_blur = blurred[i.saturating_sub(1)];
            let right_blur = blurred[(i + 1).min(input.bins.saturating_sub(1))];
            let near_avg = (left_blur + blurred[i] + right_blur) / 3.0;
            let transient_boost = (sampled[i] - prev).max(0.0) * 0.12;
            let target = clamp01(near_avg * 0.74 + micro_detail * 0.26 + transient_boost);
            let factor = if target > prev { SPECTRUM_BAR_ATTACK } else { SPECTRUM_BAR_RELEASE };
            let next = prev + (target - prev) * factor;
            scratch[i] = next * 0.88 + target * 0.12;
        }

        for i in 0..input.bins {
            let left = scratch[i.saturating_sub(1)];
            let center = scratch[i];
            let right = scratch[(i + 1).min(input.bins.saturating_sub(1))];
            let shaped = center * (1.0 - SPECTRUM_BAR_SHAPE_BLEND) + ((left + center + right) / 3.0) * SPECTRUM_BAR_SHAPE_BLEND;
            bars[i] = shaped;
            let peak_target = shaped + SPECTRUM_PEAK_RISE;
            peaks[i] = if peak_target > peaks[i] {
                peak_target
            } else {
                shaped.max(peaks[i] - SPECTRUM_PEAK_DECAY)
            };
        }

        let frame = match renderer.render(&input, &bars, &peaks) {
            Ok(frame) => frame,
            Err(err) => {
                if renderer.backend() == RasterBackend::Metal && requested_backend != RasterBackend::Metal {
                    eprintln!("metal_fallback={err}");
                    renderer = FrameRenderer::new(&input, colors, RasterBackend::Cpu)?;
                    renderer.render(&input, &bars, &peaks)?
                } else {
                    return Err(err);
                }
            }
        };

        stdout.write_all(&frame).map_err(|err| format!("failed to write frame bytes: {err}"))?;

        if frame_idx % 5 == 0 || frame_idx + 1 == total_frames {
            let progress = (((frame_idx + 1) as f32 / total_frames as f32) * 100.0).round() as usize;
            eprintln!("progress={progress}");
        }
    }

    stdout.flush().map_err(|err| format!("failed to flush stdout: {err}"))?;
    Ok(())
}
