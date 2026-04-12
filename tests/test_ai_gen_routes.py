from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from autocapcut.api.routes import ai_gen


class AIGenOutputFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        ai_gen._output_folder_state.clear()

    def test_describe_output_folder_counts_images_and_managed_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "image_001.jpg").write_bytes(b"a")
            (folder / "cover.png").write_bytes(b"b")
            (folder / "notes.txt").write_text("ignore", encoding="utf-8")

            image_count, managed_count, sample_names, video_count, video_sample_names = ai_gen._describe_output_folder(str(folder))

            self.assertEqual(image_count, 2)
            self.assertEqual(managed_count, 1)
            self.assertEqual(sample_names, ["cover.png", "image_001.jpg"])
            self.assertEqual(video_count, 0)
            self.assertEqual(video_sample_names, [])

    def test_describe_output_folder_counts_existing_videos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "clip.mp4").write_bytes(b"mp4")
            (folder / "draft.mov").write_bytes(b"mov")
            (folder / "notes.txt").write_text("ignore", encoding="utf-8")

            image_count, managed_count, sample_names, video_count, video_sample_names = ai_gen._describe_output_folder(str(folder))

            self.assertEqual(image_count, 0)
            self.assertEqual(managed_count, 0)
            self.assertEqual(sample_names, [])
            self.assertEqual(video_count, 2)
            self.assertEqual(video_sample_names, ["clip.mp4", "draft.mov"])

    def test_keep_both_reserves_non_overlapping_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "image_001.jpg").write_bytes(b"1")
            (folder / "image_002.jpg").write_bytes(b"2")

            first_start, first_count, first_removed = ai_gen._reserve_output_range(str(folder), "keep_both", 2)
            second_start, second_count, second_removed = ai_gen._reserve_output_range(str(folder), "keep_both", 1)

            self.assertEqual(first_start, 3)
            self.assertEqual(first_count, 2)
            self.assertEqual(first_removed, 0)
            self.assertEqual(second_start, 5)
            self.assertEqual(second_count, 2)
            self.assertEqual(second_removed, 0)

    def test_overwrite_starts_from_one_without_deleting_existing_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            original = folder / "image_001.jpg"
            original.write_bytes(b"old")

            start_index, image_count_before, removed_count = ai_gen._reserve_output_range(str(folder), "overwrite", 3)
            saved_path = ai_gen._save_output_image(b"new", str(folder), start_index, ".jpg")

            self.assertEqual(start_index, 1)
            self.assertEqual(image_count_before, 1)
            self.assertEqual(removed_count, 0)
            self.assertEqual(Path(saved_path).name, "image_001.jpg")
            self.assertEqual(original.read_bytes(), b"new")

    def test_replace_all_deletes_old_images_and_restarts_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "old_a.jpg").write_bytes(b"a")
            (folder / "old_b.png").write_bytes(b"b")

            start_index, image_count_before, removed_count = ai_gen._reserve_output_range(str(folder), "replace_all", 2)
            saved_path = ai_gen._save_output_image(b"fresh", str(folder), start_index, ".jpg")

            self.assertEqual(start_index, 1)
            self.assertEqual(image_count_before, 2)
            self.assertEqual(removed_count, 2)
            self.assertEqual(Path(saved_path).name, "image_001.jpg")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["image_001.jpg"])

    def test_save_output_image_uses_reserved_index_when_none_is_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            saved_path = ai_gen._save_output_image(b"payload", str(folder), None, ".jpg")

            self.assertTrue(os.path.exists(saved_path))
            self.assertEqual(Path(saved_path).name, "image_001.jpg")
