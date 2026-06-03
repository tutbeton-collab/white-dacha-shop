#!/bin/bash
# Photo analysis script - fixed version using temp files for payload

OPENROUTER_KEY=$(grep OPENROUTER_API_KEY /home/ser/.hermes/.env | head -1 | cut -d= -f2-)
MODEL="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
PHOTOS_DIR="/home/ser/.hermes/image_cache"
OUTPUT="/home/ser/Pictures/MAX_Bot/photo_analysis_batch2.json"
SUMMARY="/home/ser/Pictures/MAX_Bot/photo_analysis_summary.md"

echo "Starting analysis..."
echo "[" > "$OUTPUT"
first=true
count=0
total=$(ls "$PHOTOS_DIR"/img_*.jpg 2>/dev/null | wc -l)

for photo in "$PHOTOS_DIR"/img_*.jpg; do
    filename=$(basename "$photo")
    count=$((count + 1))
    
    # Create temp payload file
    TMPFILE=$(mktemp /tmp/photo_payload_XXXXXX.json)
    
    # Build JSON payload using python to avoid bash string issues
    python3 -c "
import json, base64
with open('$photo', 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode()
payload = {
    'model': '$MODEL',
    'messages': [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'Опиши коротко что на фото (1-2 предложения). Если овощи/ферма — какие овощи, состояние.'},
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}}
        ]
    }],
    'max_tokens': 150
}
with open('$TMPFILE', 'w') as f:
    json.dump(payload, f)
"
    
    # Try up to 3 times
    success=false
    for attempt in 1 2 3; do
        http_code=$(curl -s -o /tmp/photo_resp.json -w "%{http_code}" -X POST \
            "https://openrouter.ai/api/v1/chat/completions" \
            -H "Authorization: Bearer $OPENROUTER_KEY" \
            -H "Content-Type: application/json" \
            -d @"$TMPFILE")
        
        if [ "$http_code" = "200" ]; then
            text=$(python3 -c "
import json
with open('/tmp/photo_resp.json') as f:
    d = json.load(f)
print(d['choices'][0]['message']['content'].strip())
" 2>/dev/null | sed 's/"/\\"/g' | tr '\n' ' ')
            
            echo "[$count/$total] ✓ $filename: $text"
            
            if [ "$first" = true ]; then first=false; else echo "," >> "$OUTPUT"; fi
            echo "{\"file\": \"$filename\", \"desc\": \"$text\"}" >> "$OUTPUT"
            success=true
            break
        elif [ "$http_code" = "429" ]; then
            wait=$((attempt * 30))
            echo "[$count/$total] 429, waiting ${wait}s..."
            sleep $wait
        else
            echo "[$count/$total] ✗ HTTP $http_code"
            break
        fi
    done
    
    rm -f "$TMPFILE" /tmp/photo_resp.json
    
    if [ "$success" = false ] && [ "$http_code" != "429" ]; then
        if [ "$first" = true ]; then first=false; else echo "," >> "$OUTPUT"; fi
        echo "{\"file\": \"$filename\", \"desc\": \"ERROR: HTTP $http_code\"}" >> "$OUTPUT"
    fi
    
    sleep 15
done

echo "]" >> "$OUTPUT"

# Generate summary
python3 -c "
import json
with open('$OUTPUT') as f:
    data = json.load(f)
with open('$SUMMARY', 'w') as f:
    f.write('# Анализ фото — Белая дача\n\n')
    f.write(f'Всего: {len(data)} фото\n\n')
    for r in data:
        f.write(f'## {r[\"file\"]}\n{r[\"desc\"]}\n\n')
print(f'Summary saved: {len(data)} photos')
"

echo "Done!"
