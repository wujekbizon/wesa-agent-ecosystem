"""
split_views.py  —  splits multi-view output files into individual Razor files.

The daemon writes all views for an entity into a single Index.cshtml.
This script parses // FILE: markers and writes each section to the correct path.

Usage:
    python scripts/split_views.py --project path/to/AgentHire.Intranet
    python scripts/split_views.py --project path/to/AgentHire.PortalWWW
"""
import argparse
import os
import re
from pathlib import Path


def split_file(src: Path, project_root: Path, dry_run: bool = False) -> int:
    content = src.read_text(encoding="utf-8")
    # Split on // FILE: lines
    parts = re.split(r'^\s*//\s*FILE:\s*', content, flags=re.MULTILINE)
    if len(parts) <= 1:
        return 0  # nothing to split

    written = 0
    for part in parts[1:]:  # skip preamble before first FILE marker
        lines = part.split('\n')
        rel_path = lines[0].strip()          # e.g. Views/Agent/Create.cshtml
        body = '\n'.join(lines[1:]).lstrip('\n')
        dest = project_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            dest.write_text(body, encoding="utf-8")
        print(f"  {'[dry]' if dry_run else 'Written'}: {dest.relative_to(project_root)}")
        written += 1
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Path to the MVC project folder")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    views_root = project_root / "Views"

    if not views_root.exists():
        print(f"No Views/ folder found at {views_root}")
        return

    total = 0
    for cshtml in views_root.rglob("*.cshtml"):
        content = cshtml.read_text(encoding="utf-8")
        if "// FILE:" not in content:
            continue
        print(f"\nSplitting: {cshtml.relative_to(project_root)}")
        n = split_file(cshtml, project_root, dry_run=args.dry_run)
        if n > 0 and not args.dry_run:
            # Replace original with just the Index portion (first FILE section)
            parts = re.split(r'^\s*//\s*FILE:\s*', content, flags=re.MULTILINE)
            # Find the Index section
            index_body = None
            for part in parts[1:]:
                lines = part.split('\n')
                rel = lines[0].strip()
                if rel.endswith("Index.cshtml"):
                    index_body = '\n'.join(lines[1:]).lstrip('\n')
                    break
            if index_body:
                cshtml.write_text(index_body, encoding="utf-8")
                print(f"  Trimmed original to Index only.")
        total += n

    print(f"\nDone. {total} view file(s) written.")


if __name__ == "__main__":
    main()
