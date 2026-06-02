# Plan: Add JSON repair to xlsx_utils.py

## Changes to `file_output_utils\xlsx_utils.py`

### 1. Add `import re` after `import json`

Change:
```
import io
import json
import xlsxwriter
```
To:
```
import io
import json
import re
import xlsxwriter
```

### 2. Add JSON repair steps before `json.loads()`

At line 449-451, change:
```
        cleaned_reply = cleaned_reply.strip()

        data = json.loads(cleaned_reply)
```
To:
```
        cleaned_reply = cleaned_reply.strip()

        # Исправляем JSON: добавляем кавычки к названиям полей без них
        cleaned_reply = re.sub(
            r'(\{|\,)\s*([a-zA-Z_]\w*)\s*:',
            r'\1"\2":',
            cleaned_reply,
        )
        # Удаляем запятые перед ] и }
        cleaned_reply = re.sub(r',\s*\]', ']', cleaned_reply)
        cleaned_reply = re.sub(r',\s*\}', '}', cleaned_reply)

        data = json.loads(cleaned_reply, strict=False)
```

## Rationale

- `pdf_utils.py` and `docx_utils.py` already have the unquoted-key regex (lines 1466-1470 and 1566-1570 respectively)
- `xlsx_utils.py` was missing it, causing `json.JSONDecodeError` when LLM returns malformed JSON
- Added trailing-comma removal for extra robustness
- `strict=False` allows control characters in string values (another common LLM mistake)
