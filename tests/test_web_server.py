from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class WebSpaServingTests(unittest.TestCase):
    def test_removed_desktop_routes_are_not_mounted(self) -> None:
        from autocapcut.api.server import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}
        self.assertFalse(any(path.startswith("/api/projects") for path in paths))
        self.assertFalse(any(path.startswith("/api/sync") for path in paths))
        self.assertFalse(any(path.startswith("/api/animation") for path in paths))

    def test_documents_use_browser_download_instead_of_desktop_paths(self) -> None:
        from autocapcut.api.routes import parent_projects
        from autocapcut.api.server import app

        methods_by_path = {
            route.path: route.methods
            for route in parent_projects.router.routes
            if hasattr(route, "path") and hasattr(route, "methods")
        }
        self.assertIn(
            "GET",
            methods_by_path["/{project_id}/docs/{doc_id}/download"],
        )
        self.assertNotIn(
            "/{project_id}/docs/upload-by-path",
            methods_by_path,
        )
        self.assertNotIn(
            "/{project_id}/docs/{doc_id}/open",
            methods_by_path,
        )
        app_methods_by_path = {
            route.path: route.methods
            for route in app.routes
            if hasattr(route, "path") and hasattr(route, "methods")
        }
        self.assertIn(
            "GET",
            app_methods_by_path["/api/parent-projects/{project_id}/docs/{doc_id}/download"],
        )

    def test_self_host_script_builds_frontend_and_binds_locally(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run_web.sh").read_text(encoding="utf-8")

        self.assertIn("npm run build", script)
        self.assertIn(".venv/bin/python", script)
        self.assertIn("start_api.py", script)
        self.assertIn("--host 127.0.0.1", script)

    def test_mount_spa_serves_assets_and_browser_route_fallback(self) -> None:
        from autocapcut.api.server import mount_spa

        with TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir)
            (dist / "assets").mkdir()
            (dist / "index.html").write_text("<main>MasterOS Web</main>", encoding="utf-8")
            (dist / "assets" / "app.js").write_text("window.masteros = true", encoding="utf-8")

            app = FastAPI()

            @app.get("/api/health")
            def health() -> dict[str, str]:
                return {"status": "ok"}

            mount_spa(app, dist)

            with TestClient(app) as client:
                self.assertIn("MasterOS Web", client.get("/").text)
                self.assertIn("MasterOS Web", client.get("/projects/video-1").text)
                self.assertEqual(client.get("/assets/app.js").text, "window.masteros = true")
                missing_api = client.get("/api/not-found")
                self.assertEqual(missing_api.status_code, 404)
                self.assertEqual(missing_api.headers["content-type"], "application/json")


if __name__ == "__main__":
    unittest.main()
