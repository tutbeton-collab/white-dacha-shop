#!/bin/bash
# Photo analysis script - runs in background with no timeout

OPENROUTER_KEY=$(grep OPENROUTER_API_KEY /home/ser/.hermes/.env | cut -d= -f2)
MODEL="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
PHOTOS_DIR="/home/ser/.hermes/image_cache"
OUTPUT="/home/ser/Pictures/MAX_Bot/photo_analysis_batch2.json"
SUMMARY="/home/ser/Pictures/MAX_Bot/photo_analysis_summary.md"

echo "[" > "$OUTPUT"
first=true

for photo in "$PHOTOS_DIR"/img_*.jpg; do
    filename=$(basename "$photo")
    img_b64=$(base64 -w 0 "$photo")
    
    # Try up to 5 times
    for attempt in 1 2 3 4 5; do
        response=$(curl -s -w "\n%{http_code}" -X POST \
            "https://openrouter.ai/api/v1/chat/completions" \
            -H "Authorization: Bearer $OPENROUTER_KEY" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"$MODEL\",
                \"messages\": [{
                    \"role\": \"user\",
                    \"content\": [
                        {\"type\": \"text\", \"text\": \"Опиши коротко что на фото (1-2 предложения). Если овощи/ферма — какие овощи, состояние.\"},
                        {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/jpeg;base64,$img_b64\"}}
                    ]
                }],
                \"max_tokens\": 150
            }" 2>&1)
        
        http_code=$(echo "$response" | tail -1)
        body=$(echo "$response" | sed '$d')
        
        if [ "$http_code" = "200" ]; then
            text=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'].strip())" 2>/dev/null)
            echo "[$filename] ✓ $text"
            
            if [ "$first" = true ]; then
                first=false
            else
                echo "," >> "$OUTPUT"
            fi
            
            # Escape for JSON
            escaped_text=$(echo "$text" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
            echo "{\"file\": \"$filename\", \"desc\": ${escaped_text:-'ERROR: empty'}}" >> "$OUTPUT"
            break
        elif [ "$http_code" = "429" ]; then
            wait=$((attempt * 20))
            echo "[$filename] 429, waiting ${wait}s..."
            sleep $wait
        else
            echo "[$filename] ✗ HTTP $http_code"
            if [ "$first" = true ]; then
                first=false
            else
                echo "," >> "$OUTPUT"
            fi
            echo "{\"file\": \"$filename\", \"desc\": \"ERROR: HTTP $http_code\"}" >> "$OUTPUT"
            break
        fi
    done
    
    sleep 10
done

echo "]" >> "$OUTPUT"
echo "Done! Results saved to $OUTPUT"
