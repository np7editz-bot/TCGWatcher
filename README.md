# TCGWatcher

Dagelijkse prijs-intelligence voor sealed TCG-product op de Engelse markt. Nu: Dragon Ball Super Card Game
**Fusion World**; Bandai's **NARUTO CARD GAME** (launch juni 2027) wordt bewaakt op aankondigingen en pre-orders.

**Wat het doet, elke avond (21:30 UTC):**

1. **fetch** — haalt de dagstand van [tcgcsv.com](https://tcgcsv.com) (TCGplayer-catalogus en -prijzen) op en slaat die **ruw** op.
2. **validate** — kwaliteitspoort: schema, duplicaten, uitschieters, dekking, dag-op-dag-verschillen. Bij `fail` wordt er niets gepubliceerd.
3. **build** — Chase Score per set, prijstrends (7d/30d), `site/data/latest.json` met herkomst op elk blok.
4. **alerts** — regels → Telegram (nieuwe productlijn/pre-order, box-ratio boven drempel, prijsbewegingen, datakwaliteit).

De pagina (GitHub Pages, `site/`) toont per set de **samenstelling** (hoeveel alt-arts, Secret Rares, ★, ★★), de
verwachte waarde van die hits per box, de ratio tegen de boxprijs en de trend. Aannames zijn op de pagina aanpasbaar.

## Chase Score

```
EV_hits(set) = Σ_klasse  verwacht_aantal_per_box[klasse] × gemiddelde_marktprijs(kaarten in klasse)
ratio        = EV_hits / box-marktprijs          (★★ super alt-arts apart gerapporteerd: 1 per case, loterij)
```

Verwachte aantallen per box staan in `config/games.yaml` en zijn **schattingen** uit community-openingen, geen metingen.
Prijzen zijn TCGplayer *market price* (USD), omgerekend met de ECB-koers van de dag; waar geen market price
bestaat wordt mid/low gebruikt en dat staat erbij (`price_src`).

## Datamodel (waterdicht)

| Pad | Inhoud | Wordt herschreven? |
|---|---|---|
| `data/catalog/<cat>/_groups.json` | sets/productlijnen zoals de bron ze geeft | dagelijks (git houdt elke versie) |
| `data/catalog/<cat>/<groupId>.json` | productcatalogus van een set | alleen als de inhoud verandert |
| `data/prices/<cat>/<dag>.json` | **alle** prijsrijen van die brondag | nooit |
| `data/fx/<dag>.json` | USD→EUR-koers | nooit |
| `data/quality/<dag>.json` | validatierapport | nooit |
| `data/alerts/<dag>.json` | verstuurde/gegenereerde alerts | nooit |
| `data/state/*.json` | sync-stand, bekende groepen, actieve alert-episodes | dagelijks |
| `site/data/latest.json` | wat de pagina leest; alles herberekenbaar uit `data/` | dagelijks |

Canonieke sleutel per kaart: `GAME:SET:NUMMER:VARIANT` (bijv. `dbfw:FB10:FB10-121:SAA`), naast het
TCGplayer `productId`. Andere bronnen (Cardmarket, eBay, retailers) koppelen later op die sleutel.

## Een game toevoegen

Eén blok in `config/games.yaml`: TCGCSV-categorie, box-samenstelling, hit-klassen (regels + schatting per box),
variant-suffixen, set-overrides. Geen code nodig.

## Lokaal draaien

```bash
pip install -r requirements.txt
python -m tcgwatcher all --no-send          # fetch → validate → build → alerts (zonder Telegram)
python -m tcgwatcher build --day 2026-10-15 # alleen herbouwen uit opgeslagen data
python -m pytest -q
cd site && python -m http.server 8000       # http://localhost:8000
```

## Telegram

Repo-secrets `TELEGRAM_BOT_TOKEN` (van @BotFather) en `TELEGRAM_CHAT_ID`. Zonder token draait alles, maar
alerts blijven in `data/alerts/<dag>.json`. Zonder chat-id print de workflow-log de kandidaat-chat-id's nadat je
`/start` naar de bot hebt gestuurd.

## Roadmap

- **Fase 2** — alerts live, drempels tunen op echte verkopen (FB11 is net uit).
- **Fase 3** — EU-prijzen: Cardmarket (EN-filter) en NL/EU-retailers (voorraad, prijs, pre-order) op de canonieke sleutel.
- **Fase 4** — PWA op de telefoon met push.
- **Fase 5** — slabs: graded prijzen (PSA 9/10) naast raw → grading-gap-alerts.
