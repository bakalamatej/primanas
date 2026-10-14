# PostgreSQL Data Explorer

Lokálna desktopová aplikácia s multiplatformovým GUI (`Kivy`) na bezpečné prehliadanie PostgreSQL tabuliek podľa ERD:
`processes`, `events`, `images`, `processes_tags`, `label_metadata`.

## Funkcie

- načítanie štruktúry tabuliek priamo z PostgreSQL
- globálne vyhľadávanie v textových stĺpcoch (`ILIKE`)
- filtre podľa textu, čísel, dátumu/času a boolean hodnôt
- triedenie, `LIMIT` a `OFFSET` stránkovanie
- zobrazenie štruktúry tabuľky a primárneho kľúča

## 1. Inštalácia

Rozbaľ ZIP, otvor terminál v priečinku projektu a spusti:

### Windows

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Uprav `.env` a vyplň údaje k databáze. Súbor `.env` nezdieľaj a nepridávaj do Git-u.

## 2. Spustenie

```bash
python app.py
```

Po spustení sa otvorí samostatné okno aplikácie.