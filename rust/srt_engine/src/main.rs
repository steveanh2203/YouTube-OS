use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{self, Read};
use strsim::normalized_levenshtein;
use unicode_normalization::{char::is_combining_mark, UnicodeNormalization};

const START_SCAN_SEGMENTS: usize = 22;
const MAX_LOOKAHEAD_SEGMENTS: usize = 48;
const PREFIX_MAX_WORDS: usize = 8;
const MIN_FULL_RATIO: f64 = 0.52;
const MIN_PREFIX_RATIO: f64 = 0.46;
const MIN_SCORE: f64 = 0.54;
const MIN_COVERAGE: f64 = 0.46;
const MARGIN_FALLBACK_SCORE: f64 = 0.48;
const MARGIN_FALLBACK_FULL: f64 = 0.44;
const MARGIN_DELTA: f64 = 0.09;

static RE_NO: Lazy<Regex> = Lazy::new(|| Regex::new(r"\bno\.?\s*(\d+)\b").expect("valid regex"));
static RE_DR: Lazy<Regex> = Lazy::new(|| Regex::new(r"\bdr\.?\b").expect("valid regex"));
static RE_NON_WORD: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\w\s]").expect("valid regex"));
static RE_WS: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").expect("valid regex"));

#[derive(Debug, Deserialize)]
struct ContentItem {
    index: i64,
    text: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
struct SrtSegment {
    index: i64,
    start: String,
    end: String,
    text: String,
}

#[derive(Debug, Deserialize)]
struct MatchInput {
    content_items: Vec<ContentItem>,
    srt_segments: Vec<SrtSegment>,
}

#[derive(Debug, Serialize)]
struct MatchEntry {
    content_index: i64,
    start_segment: usize,
    end_segment: usize,
    score: f64,
    full_ratio: f64,
    prefix_ratio: f64,
    coverage: f64,
    delta: f64,
}

#[derive(Debug, Serialize)]
struct MatchOutput {
    ok: bool,
    entries: Vec<MatchEntry>,
    error: Option<String>,
}

#[derive(Clone, Copy)]
struct CandidateEval {
    score: f64,
    full_ratio: f64,
    prefix_ratio: f64,
    coverage: f64,
    end_idx: usize,
}

fn similarity(a: &str, b: &str) -> f64 {
    normalized_levenshtein(a, b)
}

fn replace_number_word(token: &str) -> &str {
    match token {
        "zero" => "0",
        "one" => "1",
        "two" => "2",
        "three" => "3",
        "four" => "4",
        "five" => "5",
        "six" => "6",
        "seven" => "7",
        "eight" => "8",
        "nine" => "9",
        "ten" => "10",
        "eleven" => "11",
        "twelve" => "12",
        "thirteen" => "13",
        "fourteen" => "14",
        "fifteen" => "15",
        "sixteen" => "16",
        "seventeen" => "17",
        "eighteen" => "18",
        "nineteen" => "19",
        "twenty" => "20",
        "thirty" => "30",
        "forty" => "40",
        "fifty" => "50",
        "sixty" => "60",
        "seventy" => "70",
        "eighty" => "80",
        "ninety" => "90",
        _ => token,
    }
}

fn normalize(text: &str) -> String {
    let lower = text.to_lowercase();
    let replaced_no = RE_NO.replace_all(&lower, "number $1").to_string();
    let no_diacritics: String = replaced_no
        .nfd()
        .filter(|c| !is_combining_mark(*c))
        .collect();
    let replaced_dr = RE_DR.replace_all(&no_diacritics, "doctor").to_string();
    let stripped = RE_NON_WORD.replace_all(&replaced_dr, " ").to_string();
    let compact = RE_WS.replace_all(&stripped, " ").trim().to_string();
    if compact.is_empty() {
        return String::new();
    }
    compact
        .split_whitespace()
        .map(replace_number_word)
        .collect::<Vec<&str>>()
        .join(" ")
}

fn coverage_ratio(acc_words: &[String], content_words: &[String]) -> f64 {
    if content_words.is_empty() {
        return 0.0;
    }
    let mut content_counter: HashMap<&str, usize> = HashMap::new();
    let mut acc_counter: HashMap<&str, usize> = HashMap::new();

    for token in content_words {
        *content_counter.entry(token.as_str()).or_insert(0) += 1;
    }
    for token in acc_words {
        *acc_counter.entry(token.as_str()).or_insert(0) += 1;
    }

    let mut overlap = 0usize;
    for (token, cnt) in content_counter {
        let acc_cnt = *acc_counter.get(token).unwrap_or(&0usize);
        overlap += cnt.min(acc_cnt);
    }
    overlap as f64 / content_words.len() as f64
}

fn score_start_candidate(
    start_idx: usize,
    content_norm: &str,
    content_words: &[String],
    normalized_segment_words: &[Vec<String>],
) -> CandidateEval {
    let n_content_words = content_words.len().max(1);
    let max_words = ((n_content_words as f64 * 1.9) as usize).max(n_content_words + 12);

    let mut accumulated_words: Vec<String> = Vec::new();
    let mut best = CandidateEval {
        score: -1.0,
        full_ratio: 0.0,
        prefix_ratio: 0.0,
        coverage: 0.0,
        end_idx: start_idx,
    };

    let mut look = start_idx;
    while look < normalized_segment_words.len() && (look - start_idx) < MAX_LOOKAHEAD_SEGMENTS {
        let seg_words = &normalized_segment_words[look];
        if !seg_words.is_empty() {
            for w in seg_words {
                accumulated_words.push(w.clone());
            }
            let acc_norm = accumulated_words.join(" ");
            let full_ratio = similarity(&acc_norm, content_norm);

            let prefix_words = PREFIX_MAX_WORDS
                .min(accumulated_words.len())
                .min(content_words.len());
            let mut prefix_ratio = 0.0;
            if prefix_words > 0 {
                let acc_prefix = accumulated_words[0..prefix_words].join(" ");
                let content_prefix = content_words[0..prefix_words].join(" ");
                prefix_ratio = similarity(&acc_prefix, &content_prefix);
            }

            let coverage = coverage_ratio(&accumulated_words, content_words);
            let len_penalty = ((accumulated_words.len() as isize - n_content_words as isize).abs() as f64)
                / n_content_words as f64;
            let score = (full_ratio * 0.60) + (prefix_ratio * 0.20) + (coverage * 0.20) - (len_penalty * 0.08);

            if score > best.score {
                best = CandidateEval {
                    score,
                    full_ratio,
                    prefix_ratio,
                    coverage,
                    end_idx: look,
                };
            }

            if full_ratio >= 0.95 && coverage >= 0.80 && accumulated_words.len() >= (n_content_words as f64 * 0.8) as usize {
                break;
            }
            if accumulated_words.len() >= max_words {
                break;
            }
        }
        look += 1;
    }
    best
}

fn run_match(input: MatchInput) -> MatchOutput {
    if input.content_items.is_empty() {
        return MatchOutput {
            ok: true,
            entries: vec![],
            error: None,
        };
    }
    if input.srt_segments.is_empty() {
        return MatchOutput {
            ok: false,
            entries: vec![],
            error: Some("No SRT segments".to_string()),
        };
    }

    let normalized_segment_words: Vec<Vec<String>> = input
        .srt_segments
        .iter()
        .map(|seg| normalize(&seg.text).split_whitespace().map(|s| s.to_string()).collect())
        .collect();

    if normalized_segment_words.iter().all(|ws| ws.is_empty()) {
        return MatchOutput {
            ok: false,
            entries: vec![],
            error: Some("SRT has no text tokens".to_string()),
        };
    }

    let mut srt_ptr: usize = 0;
    let total = input.srt_segments.len();
    let mut entries: Vec<MatchEntry> = Vec::with_capacity(input.content_items.len());

    for content in &input.content_items {
        if srt_ptr >= total {
            return MatchOutput {
                ok: false,
                entries: vec![],
                error: Some(format!("Ran out of SRT segments at content #{}", content.index)),
            };
        }

        let content_norm = normalize(&content.text);
        let content_words: Vec<String> = content_norm
            .split_whitespace()
            .map(|s| s.to_string())
            .collect();
        if content_words.is_empty() {
            return MatchOutput {
                ok: false,
                entries: vec![],
                error: Some(format!("Empty normalized content #{}", content.index)),
            };
        }

        let scan_end = (srt_ptr + START_SCAN_SEGMENTS).min(total);

        let mut chosen_start: Option<usize> = None;
        let mut chosen_eval = CandidateEval {
            score: -1.0,
            full_ratio: 0.0,
            prefix_ratio: 0.0,
            coverage: 0.0,
            end_idx: srt_ptr,
        };
        let mut second_best_score = -1.0f64;

        for start_idx in srt_ptr..scan_end {
            if normalized_segment_words[start_idx].is_empty() {
                continue;
            }
            let eval = score_start_candidate(start_idx, &content_norm, &content_words, &normalized_segment_words);

            let better = eval.score > chosen_eval.score + 0.005;
            let tie = (eval.score - chosen_eval.score).abs() <= 0.005;
            let chosen_start_cmp = chosen_start.unwrap_or(0);

            let tie_break = tie
                && (eval.prefix_ratio > chosen_eval.prefix_ratio + 0.01
                    || ((eval.prefix_ratio - chosen_eval.prefix_ratio).abs() <= 0.01
                        && eval.coverage > chosen_eval.coverage + 0.01)
                    || ((eval.prefix_ratio - chosen_eval.prefix_ratio).abs() <= 0.01
                        && (eval.coverage - chosen_eval.coverage).abs() <= 0.01
                        && eval.full_ratio > chosen_eval.full_ratio + 0.01)
                    || ((eval.prefix_ratio - chosen_eval.prefix_ratio).abs() <= 0.01
                        && (eval.coverage - chosen_eval.coverage).abs() <= 0.01
                        && (eval.full_ratio - chosen_eval.full_ratio).abs() <= 0.01
                        && start_idx > chosen_start_cmp));

            if better || tie_break {
                if chosen_eval.score > second_best_score {
                    second_best_score = chosen_eval.score;
                }
                chosen_start = Some(start_idx);
                chosen_eval = eval;
            } else if eval.score > second_best_score {
                second_best_score = eval.score;
            }
        }

        let start_idx = match chosen_start {
            Some(v) => v,
            None => {
                return MatchOutput {
                    ok: false,
                    entries: vec![],
                    error: Some(format!("No confident match for content #{}", content.index)),
                }
            }
        };
        let end_idx = chosen_eval.end_idx;

        let strong_confidence = chosen_eval.full_ratio >= MIN_FULL_RATIO
            && chosen_eval.prefix_ratio >= MIN_PREFIX_RATIO
            && chosen_eval.coverage >= MIN_COVERAGE
            && chosen_eval.score >= MIN_SCORE;
        let margin_confidence = chosen_eval.score >= MARGIN_FALLBACK_SCORE
            && chosen_eval.full_ratio >= MARGIN_FALLBACK_FULL
            && (chosen_eval.score - second_best_score) >= MARGIN_DELTA;

        if !(strong_confidence || margin_confidence) {
            return MatchOutput {
                ok: false,
                entries: vec![],
                error: Some(format!("Low confidence at content #{}", content.index)),
            };
        }

        entries.push(MatchEntry {
            content_index: content.index,
            start_segment: start_idx,
            end_segment: end_idx,
            score: chosen_eval.score,
            full_ratio: chosen_eval.full_ratio,
            prefix_ratio: chosen_eval.prefix_ratio,
            coverage: chosen_eval.coverage,
            delta: chosen_eval.score - second_best_score,
        });
        srt_ptr = end_idx + 1;
    }

    MatchOutput {
        ok: true,
        entries,
        error: None,
    }
}

fn main() {
    let mut input_buf = String::new();
    if let Err(exc) = io::stdin().read_to_string(&mut input_buf) {
        let output = MatchOutput {
            ok: false,
            entries: vec![],
            error: Some(format!("stdin read error: {}", exc)),
        };
        let _ = println!("{}", serde_json::to_string(&output).unwrap_or_else(|_| "{\"ok\":false,\"entries\":[],\"error\":\"serialization error\"}".to_string()));
        return;
    }

    let parsed: MatchInput = match serde_json::from_str(&input_buf) {
        Ok(v) => v,
        Err(exc) => {
            let output = MatchOutput {
                ok: false,
                entries: vec![],
                error: Some(format!("invalid input json: {}", exc)),
            };
            let _ = println!("{}", serde_json::to_string(&output).unwrap_or_else(|_| "{\"ok\":false,\"entries\":[],\"error\":\"serialization error\"}".to_string()));
            return;
        }
    };

    let output = run_match(parsed);
    let serialized = serde_json::to_string(&output).unwrap_or_else(|_| {
        "{\"ok\":false,\"entries\":[],\"error\":\"serialization error\"}".to_string()
    });
    println!("{}", serialized);
}
