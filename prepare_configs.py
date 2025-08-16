import os
import shutil

# This script should be run from the project's root directory.
ROOT_DIR = os.path.abspath(os.curdir)
TEMPLATE_DIR = os.path.join(ROOT_DIR, "config", "templates")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")


def prepare_configurations():
    """
    Checks for configuration templates and copies them to the main config
    directory if the corresponding config file does not already exist.
    """
    print("--- Preparing Configurations ---")

    if not os.path.isdir(TEMPLATE_DIR):
        print(f"Error: Template directory not found at {TEMPLATE_DIR}")
        return

    if not os.path.isdir(CONFIG_DIR):
        print(f"Creating configuration directory at {CONFIG_DIR}")
        os.makedirs(CONFIG_DIR)

    templates = [f for f in os.listdir(TEMPLATE_DIR) if f.endswith('.template')]

    if not templates:
        print("No template files found.")
        return

    for template_name in sorted(templates):
        template_path = os.path.join(TEMPLATE_DIR, template_name)
        target_name = template_name.replace(".template", "")
        target_path = os.path.join(CONFIG_DIR, target_name)

        if not os.path.exists(target_path):
            shutil.copy(template_path, target_path)
            print(f"✅ Created '{target_name}' from template.")
        else:
            print(f"ℹ️  '{target_name}' already exists. Skipping.")

    print("\n--- Configuration setup complete ---")


if __name__ == "__main__":
    prepare_configurations()
