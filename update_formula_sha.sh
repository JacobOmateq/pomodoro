#!/bin/bash
# Helper script to update the SHA256 in the Homebrew formula
# Usage: ./update_formula_sha.sh [version_tag]

VERSION=${1:-main}
FORMULA_FILE="Formula/pomodoro.rb"
TEMP_ZIP=$(mktemp)

echo "Fetching repository archive for version: $VERSION"
curl -L "https://github.com/JacobOmateq/pomodoro/archive/refs/heads/${VERSION}.zip" -o "$TEMP_ZIP"

if [ $? -ne 0 ]; then
    echo "Error: Failed to download archive"
    rm -f "$TEMP_ZIP"
    exit 1
fi

SHA256=$(shasum -a 256 "$TEMP_ZIP" | awk '{print $1}')
echo "SHA256: $SHA256"

# Update the formula file
if [ -f "$FORMULA_FILE" ]; then
    # Use sed to replace the sha256 line
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/sha256 \".*\"/sha256 \"$SHA256\"/" "$FORMULA_FILE"
    else
        # Linux
        sed -i "s/sha256 \".*\"/sha256 \"$SHA256\"/" "$FORMULA_FILE"
    fi
    echo "Updated $FORMULA_FILE with SHA256: $SHA256"
else
    echo "Warning: $FORMULA_FILE not found"
fi

rm -f "$TEMP_ZIP"

