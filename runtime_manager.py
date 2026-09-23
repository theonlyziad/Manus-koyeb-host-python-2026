from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENTRYPOINTS = ("main.py", "bot.py", "app.py", "run.py", "index.py")
MAX_REQUIREMENTS_BYTES = 256 * 1024
IMPORT_TO_PACKAGE = {
    "aiogram": "aiogram>=3.0,<4",
    "telegram": "python-telegram-bot>=21,<23",
    "pyrogram": "pyrogram==2.0.106",
    "TgCrypto": "TgCrypto>=1.2.5",
    "kvsqlite": "kvsqlite",
    "aiohttp": "aiohttp>=3.12,<4",
    "requests": "requests>=2.32,<3",
    "bs4": "beautifulsoup4>=4.12,<5",
    "openai": "openai>=1.40,<2",
    "dotenv": "python-dotenv>=1,<2",
    "urllib3": "urllib3>=2,<3",
    "PIL": "Pillow>=10,<12",
    "psutil": "psutil>=7,<8",
}
HOST_SECRET_NAMES = {"BOT_TOKEN", "OWNER_ID", "ADMIN_ID", "YOUR_USERNAME", "UPDATE_CHANNEL", "FORCE_CHANNEL"}


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("unsafe path")
    return candidate


def find_requirements(bot_path: Path, user_root: Path) -> Path | None:
    candidates = [bot_path.parent / "requirements.txt", user_root / "requirements.txt"]
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size <= MAX_REQUIREMENTS_BYTES:
            return candidate
    return None


def find_entrypoint(path: Path) -> Path:
    if path.is_file():
        if path.suffix.lower() not in {".py", ".js"}:
            raise ValueError("only Python and JavaScript entrypoints are supported")
        return path
    for name in ENTRYPOINTS:
        candidate = path / name
        if candidate.is_file():
            return candidate
    py_files = sorted(path.glob("*.py"))
    if len(py_files) == 1:
        return py_files[0]
    raise FileNotFoundError("no entrypoint found; add main.py or bot.py")


def infer_requirements(entrypoint: Path) -> Path | None:
    if entrypoint.suffix.lower() != ".py":
        return None
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return None
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    packages = {IMPORT_TO_PACKAGE[name] for name in imports if name in IMPORT_TO_PACKAGE}
    if "pyrogram" in imports:
        packages.add("TgCrypto>=1.2.5")
    packages = sorted(packages)
    if not packages:
        return None
    target = entrypoint.parent / "requirements.auto.txt"
    target.write_text("# Auto-generated from imports\n" + "\n".join(packages) + "\n", encoding="utf-8")
    return target


def install_requirements(requirements: Path, runtime_dir: Path) -> Path:
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    venv_dir = runtime_dir / ".venv"
    marker = runtime_dir / ".requirements.sha256"
    python_bin = venv_dir / "bin" / "python"
    cached = marker.is_file() and marker.read_text(encoding="ascii").strip() == digest
    if not python_bin.exists() or not cached:
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=120)
        except (subprocess.CalledProcessError, FileNotFoundError):
            if venv_dir.exists():
                shutil.rmtree(venv_dir)
            subprocess.run([sys.executable, "-m", "virtualenv", "--no-download", str(venv_dir)], check=True, timeout=120)
        subprocess.run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"], check=True, timeout=180)
        subprocess.run([str(python_bin), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-r", str(requirements)], check=True, timeout=600)
        marker.write_text(digest, encoding="ascii")
    return python_bin


def prepare_command(entrypoint: Path, user_root: Path) -> list[str]:
    relative = entrypoint.resolve().relative_to(user_root.resolve())
    entrypoint = safe_path(user_root, str(relative))
    requirements = find_requirements(entrypoint, user_root) or infer_requirements(entrypoint)
    if entrypoint.suffix.lower() == ".py":
        runtime_dir = entrypoint.parent / ".runtime" / entrypoint.stem
        python_bin = install_requirements(requirements, runtime_dir) if requirements else Path(sys.executable)
        return [str(python_bin), "-u", str(entrypoint)]
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is not installed in this Host image")
    return [node, str(entrypoint)]


def child_environment(entrypoint: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in HOST_SECRET_NAMES}
    env_file = entrypoint.parent / ".env"
    if env_file.is_file() and env_file.stat().st_size <= 256 * 1024:
        for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key.replace("_", "").isalnum():
                environment[key] = value.strip().strip("\"'")
    return environment
