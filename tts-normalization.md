# TTS Text Normalization Patterns

Language-specific text normalization rules applied before sending text to ElevenLabs TTS. These rules expand abbreviations, units, and symbols into their spoken forms so the TTS engine pronounces them naturally.

## Normalization Pipeline Order

Text is processed in this order:

1. **Language-specific normalization** — expand units, abbreviations, and symbols for the target language (from `lang_config.json` → `normalization`)
2. **Universal TTS replacements** — pronunciation fixes for brand names and terms that the TTS mispronounces (from `lang_config.json` → `tts_replacements`)
3. **Final cleanup** — trim extra whitespace, normalize unicode

Each step uses regex substitution via `re.sub()`. Rules are applied in the order they appear in `lang_config.json`.

## English Normalization Rules

| Pattern | Expansion | Example |
|---------|-----------|---------|
| `\b(\d+)ft\b` | `\1 feet` | "20ft" → "20 feet" |
| `\b(\d+)in\b` | `\1 inches` | "6in" → "6 inches" |
| `\b(\d+)lb\b` | `\1 pounds` | "50lb" → "50 pounds" |
| `\b(\d+)°F\b` | `\1 degrees Fahrenheit` | "100°F" → "100 degrees Fahrenheit" |
| `\b(\d+)°C\b` | `\1 degrees Celsius` | "37°C" → "37 degrees Celsius" |
| `\b(\d+)psi\b` | `\1 P S I` | "150psi" → "150 P S I" |
| `\be\.g\.` | `for example` | "e.g." → "for example" |
| `\bi\.e\.` | `that is` | "i.e." → "that is" |
| `\betc\.` | `etcetera` | "etc." → "etcetera" |
| `\bvs\.?\b` | `versus` | "vs." → "versus" |
| `\bw/\b` | `with` | "w/" → "with" |
| `\bw/o\b` | `without` | "w/o" → "without" |
| `\b24/7\b` | `twenty four seven` | "24/7" → "twenty four seven" |

## Spanish Normalization Rules

| Pattern | Expansion | Example |
|---------|-----------|---------|
| `\b(\d+)\s?m\b` | `\1 metros` | "20m" → "20 metros" |
| `\b(\d+)\s?cm\b` | `\1 centímetros` | "50cm" → "50 centímetros" |
| `\b(\d+)\s?kg\b` | `\1 kilogramos` | "100kg" → "100 kilogramos" |
| `\b(\d+)°C\b` | `\1 grados centígrados` | "37°C" → "37 grados centígrados" |
| `\b(\d+)°F\b` | `\1 grados Fahrenheit` | "100°F" → "100 grados Fahrenheit" |
| `\bp\.\s?ej\.` | `por ejemplo` | "p. ej." → "por ejemplo" |
| `\betc\.` | `etcétera` | "etc." → "etcétera" |
| `\bNº\s?(\d+)` | `número \1` | "Nº 5" → "número 5" |
| `\b24/7\b` | `veinticuatro siete` | "24/7" → "veinticuatro siete" |

## Adding New Rules

To add normalization rules for a new language or extend existing rules:

1. Open `lang_config.json`
2. Under the target language key, add entries to the `normalization` object
3. Keys are regex patterns (use `\\b` for word boundaries since JSON requires escaping backslashes)
4. Values are replacement strings (can use `\\1` for capture groups)
5. Rules are applied in order — put more specific patterns before general ones

**Example — adding a French config:**
```json
{
  "fr": {
    "voice_id": "...",
    "voice_settings": { ... },
    "tts_replacements": { ... },
    "normalization": {
      "\\b(\\d+)\\s?m\\b": "\\1 mètres",
      "\\b(\\d+)\\s?cm\\b": "\\1 centimètres",
      "\\b(\\d+)°C\\b": "\\1 degrés Celsius",
      "\\betc\\.": "et cetera",
      "\\bc-à-d\\.?": "c'est-à-dire"
    }
  }
}
```

## Implementation in synthesize_tts.py

The `normalize_for_tts()` function applies these rules:

```python
def normalize_for_tts(text: str, normalization: dict[str, str]) -> str:
    """Apply language-specific normalization rules to text before TTS."""
    for pattern, replacement in normalization.items():
        text = re.sub(pattern, replacement, text)
    return text
```

This runs before `preprocess_tts_text()` (which handles brand-name replacements). The full preprocessing chain is:

```
raw text → normalize_for_tts(text, normalization) → preprocess_tts_text(text, replacements) → ElevenLabs TTS
```


## Assembly Audio Post-Processing Presets

`assemble_video.py` consumes optional post-processing settings from `lang_config.json` under each language's `audio_postprocessing` key, plus optional `voice_overrides.<voice_id>.audio_postprocessing` blocks.

### Supported Controls

| Key | Purpose | Typical Range |
|-----|---------|---------------|
| `presence_eq.gain_db` | Presence lift around 3kHz for intelligibility | `0.0` to `2.0` dB |
| `presence_eq.enabled` | Toggle presence EQ stage | `true/false` |
| `highpass.frequency` | Cut low-frequency rumble | `60` to `110` Hz |
| `highpass.enabled` | Toggle high-pass stage | `true/false` |
| `limiter.intensity` | Limiter strength (higher = stricter peak control) | `0.0` to `1.0` |
| `deesser.enabled` + `deesser.intensity` | Optional sibilance control for harsh “s” voices | `0.15` to `0.45` |
| `loudnorm.two_pass` | Two-pass loudness normalization for long-form consistency | `true/false` |

### Recommended Presets

- **General narration (default)**
  - `presence_eq.gain_db: 1.2-1.5`
  - `highpass.frequency: 80-90`
  - `limiter.intensity: 0.9-1.0`
  - `deesser.enabled: false`
  - `loudnorm.two_pass: false`

- **Sibilant voice / harsh highs**
  - `deesser.enabled: true`, `deesser.intensity: 0.25-0.40`
  - reduce `presence_eq.gain_db` to `0.6-1.0`
  - keep `highpass.frequency` near `80-90`

- **Long-form training (>15 min)**
  - keep normal EQ/high-pass settings
  - set `loudnorm.two_pass: true` for better inter-slide loudness stability
  - optionally lower `limiter.intensity` slightly (`0.75-0.9`) to preserve dynamics

### Override Example (`lang_config.json`)

```json
{
  "en": {
    "audio_postprocessing": {
      "presence_eq": { "enabled": true, "gain_db": 1.4 },
      "highpass": { "enabled": true, "frequency": 80 },
      "limiter": { "enabled": true, "intensity": 0.95 },
      "loudnorm": { "enabled": true, "two_pass": false, "I": -16, "LRA": 11, "TP": -1.5 }
    },
    "voice_overrides": {
      "SIBILANT_EN_SAMPLE": {
        "audio_postprocessing": {
          "deesser": { "enabled": true, "intensity": 0.35, "frequency": 6200 },
          "presence_eq": { "gain_db": 0.8 },
          "loudnorm": { "two_pass": true }
        }
      }
    }
  }
}
```
