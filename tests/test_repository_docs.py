"""Keep key project presentation and safety claims synchronized with the repository."""
from pathlib import Path
import re
import subprocess

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ["README.md", "docs/CONFIGURATION.md", "docs/OPERATIONS.md", "docs/HOSTING.md",
             "docs/ANTINUKE.md", "docs/PERMISSIONS.md", "docs/MASTER_AUDIT.md", "docs/DEVELOPER_COMMANDS.md",
             "docs/DEVELOPERS.md", "docs/REPOSITORY_AUDIT.md", ".github/CONTRIBUTING.md", ".github/SECURITY.md"]


def _anchors(text):
    """GitHub-like heading slugs for the simple headings used in these documents."""
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.M):
        slug = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
        result.add(slug)
    return result


def test_documentation_local_links_and_headings_exist():
    broken = []
    for name in DOCUMENTS:
        source = ROOT / name
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)\s]+)\)", text):
            if re.match(r"[a-z]+://", target) or target.startswith("mailto:"):
                continue
            path, _, fragment = target.partition("#")
            dest = (source.parent / path).resolve() if path else source
            if not dest.exists():
                broken.append(f"{name}: {target} (missing file)")
            elif fragment and dest.suffix.lower() == ".md" and fragment not in _anchors(dest.read_text()):
                broken.append(f"{name}: {target} (missing heading)")
    assert not broken, "\n".join(broken)


def test_example_secrets_are_empty():
    values = dotenv_values(ROOT / ".env.example")
    secrets = ("DISCORD_TOKEN", "AI_API_KEY", "OCR_SPACE_API_KEY", "LASTFM_API_KEY", "SPOTIFY_CLIENT_SECRET",
               "TWITCH_CLIENT_SECRET", "LAVALINK_PASSWORD", "DISCORD_CLIENT_SECRET", "DASHBOARD_SECRET", "NGROK_AUTHTOKEN")
    assert all(not values.get(name) for name in secrets)


def test_git_ignores_private_environment_and_database_files():
    if not (ROOT / ".git").exists():
        pytest.skip("Git ignore rules cannot be checked outside a Git checkout")
    names = [".env", ".env.production", "prod.env", "private.sqlite3", "private.db", "private.pem"]
    result = subprocess.run(["git", "check-ignore", *names], cwd=ROOT, capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) == set(names)


def test_command_reference_includes_prefix_and_permission_caveat():
    text = (ROOT / "docs/COMMANDS.md").read_text(encoding="utf-8")
    assert "` ,`" not in text
    assert "`,antinuke status`" in text
    assert "not a complete authorization policy" in text
    assert "Hidden developer commands are intentionally omitted" in text
    assert "`,antinukepreset " not in text


def test_developer_reference_is_separate_and_uses_prefix():
    text = (ROOT / "docs/DEVELOPER_COMMANDS.md").read_text(encoding="utf-8")
    assert "`,authorize <guild_id>" in text
    assert "`,system`" in text
    assert "require developer/owner authorization" in text


def test_permission_guide_discloses_builtins_and_self_role_limitations():
    from core import perms

    guide = (ROOT / "docs/PERMISSIONS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Self-role safety" in guide and "Self-role safety" in readme
    assert "Existing role holders are not removed" in guide
    assert "concurrent policy changes can race a grant" in guide
    for uid, name in {**perms.TRUSTED_BOTS, **perms.WHITELISTED_PEOPLE}.items():
        assert str(uid) in guide and name in guide
    assert "maximum one hour" in guide
    assert "not the complete" in (ROOT / "docs/MASTER_AUDIT.md").read_text(encoding="utf-8")
