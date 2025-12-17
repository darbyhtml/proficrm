import os
from pathlib import Path
import django
from django.template import loader

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm.settings')
django.setup()

root = Path('templates')
files = sorted(root.rglob('*.html'))
errors = []

for f in files:
    rel = f.relative_to(root).as_posix()
    try:
        loader.get_template(rel)
    except Exception as e:
        errors.append((rel, repr(e)))

if errors:
    print('TEMPLATE_COMPILE_ERRORS:', len(errors))
    for rel, e in errors[:50]:
        print('-', rel, e)
    raise SystemExit(1)

print('TEMPLATE_COMPILE_OK:', len(files))
