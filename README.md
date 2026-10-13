# PostgreSQL Data Explorer

Lokálna desktopová aplikácia s multiplatformovým GUI (`Kivy`) na bezpečné prehliadanie PostgreSQL tabuliek podľa ERD:
`processes`, `events`, `images`, `processes_tags`, `label_metadata`.

## Funkcie

- načítanie štruktúry tabuliek priamo z PostgreSQL
- globálne vyhľadávanie v textových stĺpcoch (`ILIKE`)
- filtre podľa textu, čísel, dátumu/času a boolean hodnôt
- triedenie, `LIMIT` a `OFFSET` stránkovanie
- zobrazenie štruktúry tabuľky a primárneho kľúča
- žiadne zápisové operácie z aplikácie
- obmedzenie dotazu na 15 sekúnd

## 1. Požiadavky

- Python 3.9 alebo novší
- prístup na PostgreSQL server z počítača, na ktorom aplikácia pobeží
- databázový účet s právami iba na čítanie

## 2. Inštalácia

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

## 3. Odporúčaný read-only účet

Administrátor databázy môže vytvoriť samostatný účet. Nahraď názov databázy, schému a heslo skutočnými hodnotami:

```sql
CREATE ROLE teamcore_reader LOGIN PASSWORD 'SEM_DAJ_DLHE_UNIKATNE_HESLO';
GRANT CONNECT ON DATABASE nazov_databazy TO teamcore_reader;
GRANT USAGE ON SCHEMA public TO teamcore_reader;
GRANT SELECT ON TABLE
    public.processes,
    public.events,
    public.images,
    public.processes_tags,
    public.label_metadata
TO teamcore_reader;
```

Ak sa dáta nachádzajú v inej schéme, zmeň `public`. Tento účet nepoužívaj na zápis alebo správu schémy.

## 4. Spustenie

```bash
python app.py
```

Po spustení sa otvorí samostatné okno aplikácie. Kivy poskytuje rovnaké GUI widgety na macOS aj Windows.

## 6. Výkon pri miliónoch riadkov

Aplikácia neťahá celú tabuľku do pamäte. Každý dotaz vracia najviac zvolený počet riadkov plus jeden záznam na zistenie, či existuje ďalšia stránka. Filtre a stránkovanie sa vykonávajú na PostgreSQL serveri.

- Pri bežnom používaní najprv filtruj podľa ID, času alebo iného selektívneho stĺpca.
- `OFFSET` je pri veľmi hlbokých stránkach pomalší. Pre produkčné používanie s veľkým objemom dát je vhodné doplniť keyset/cursor stránkovanie podľa stabilného unikátneho kľúča.
- Globálne hľadanie s úvodným `%` môže byť pri miliónoch riadkov pomalé. Na časté full-text vyhľadávanie zváž PostgreSQL trigram index (`pg_trgm`) alebo full-text index, ktoré musí nastaviť správca databázy.
- Indexy vytváraj až po kontrole existujúcich indexov a typických dotazov. Nepridávaj indexy naslepo na produkčnú databázu.
- `statement_timeout` je nastavený na 15 sekúnd. Ak má databáza dlhšie oprávnené dotazy, hodnotu uprav podľa prevádzkových požiadaviek.

## Poznámky

Aplikácia automaticky zisťuje stĺpce a typy v databáze, takže drobné rozdiely oproti obrázku ERD nevyžadujú zmenu kódu. Predvolene sú povolené len tabuľky uvedené v `PGTABLES`.
