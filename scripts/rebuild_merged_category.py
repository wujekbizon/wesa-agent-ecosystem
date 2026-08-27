import argparse

from merge_utils import rebuild_category


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rebuild a category merge from all complete validated manifests."
    )
    parser.add_argument("--category", required=True)
    arguments = parser.parse_args()

    result = rebuild_category(arguments.category)
    action = "Rebuilt" if result.changed else "Verified unchanged"
    print(f"{action} {result.output_path} with {result.question_count} questions.")
    if result.skipped_manifests:
        print("Skipped incomplete manifests:")
        for manifest_name in result.skipped_manifests:
            print(f"- {manifest_name}")
