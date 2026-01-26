
import os

file_path = 'requirements.txt'

# Try reading with different encodings
content = ""
try:
    with open(file_path, 'r', encoding='utf-16-le') as f:
        content = f.read()
except Exception:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        with open(file_path, 'r', encoding='mbcs') as f: # Windows default
            content = f.read()

lines = content.split('\n')
valid_lines = []

for line in lines:
    line = line.strip()
    if not line:
        continue
    # Simple heuristic to keep valid package lines (alphanumeric, -, =, ., _)
    # Discard lines with non-ascii garbage
    if all(ord(c) < 128 for c in line):
        valid_lines.append(line)

# Ensure django-axes is there
if 'django-axes' not in valid_lines:
    valid_lines.append('django-axes')

# Remove duplicate if exists (e.g. from previous failed append)
valid_lines = sorted(list(set(valid_lines)))

with open(file_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(valid_lines))

print("Fixed requirements.txt")
