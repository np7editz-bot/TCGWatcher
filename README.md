# TCGWatcher

Dagelijkse prijs-intelligence voor sealed TCG-product op de Engelse markt. Nu: Dragon Ball Super Card Game
**Fusion World**; Bandai's **NARUTO CARD GAME** (launch juni 2027) wordt bewaakt op aankondigingen en pre-orders.

**Wat het doet, elke avond (21:30 UTC):**

1. **fetch** — haalt de dagstand van [tcgcsv.com](https://tcgcsv.com) (TCGplayer-catalogus en -prijzen) op en slaat die **ruw** op.
2. **validate** — kwaliteitspoort: schema, duplicaten, uitschieters, dekking, dag-op-dag-verschillen. Bij `fail` wordt er niets gepubliceerd.
3. **build** — Chase Score per set, prijstrends (7d/30d), `site/data/latest.json` met herkomst op elk blok.
4. **alerts** — regels → Telegram (nieuwe productlijn/pre-order, box-ratio boven drempel, prijsbewegingen, datakwaliteit).
5. **retail** (ook elke 2 uur via `retail.yml`) — EU-webshops uit `config/retailers.yaml`: prijs, voorraad en pre-orderstatus per sealed product; alerts bij *pre-order open*, *terug op voorraad*, *uitverkocht*, prijs ±10%. Laagste EU-prijs per set op de pagina.

De pagina (`site/`, statisch te hosten; GitHub Pages vereist een publieke repo) toont per set de **samenstelling** (hoeveel alt-arts, Secret Rares, ★, ★★), de
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
| `data/quality/<dag>.json` | validatierapport (afgeleid) | alleen bij herbouw met nieuwe code |
| `data/alerts/<dag>.json` | gegenereerde alerts (afgeleid) | alleen bij herbouw met nieuwe code |
| `data/retail/<dag>.json` | alle retailer-observaties van die dag (append-only) | nooit |
| `data/cardtrader/<dag>.json` | CardTrader blueprints (incl. `tcg_player_id`, `card_market_ids`) en EN/EUR-aanbod per kaart | nooit |
| `data/state/*.json` | sync-stand, bekende groepen, actieve alert-episodes, laatste retailer-status | dagelijks |
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

Berichten: één **dagelijkse briefing** (ranking met verschuiving, bewegingen ≥20%, boxen, retail, Naruto) plus **signaalkaarten** met foto en knoppen voor koopsignalen, nieuwe productlijnen, nieuwe sealed producten, datakwaliteit (alleen bij wijziging) en retail-overgangen. Stuur `/mute FB10`, `/unmute FB10`, `/mute all` of `/status` naar de bot; wordt bij de volgende run verwerkt.


Repo-secrets `TELEGRAM_BOT_TOKEN` (van @BotFather) en `TELEGRAM_CHAT_ID`. Zonder token draait alles, maar
alerts blijven in `data/alerts/<dag>.json`. Zonder chat-id print de workflow-log de kandidaat-chat-id's nadat je
`/start` naar de bot hebt gestuurd.

## Roadmap

- **Fase 2** — alerts live, drempels tunen op echte verkopen (FB11 is net uit).
- **Fase 3a** — retailer-monitor (gedaan; shops bijvoegen = regel in `config/retailers.yaml`).
- **Fase 3b** — CardTrader EU-prijzen (officiële API, EN/EUR, laagste echte aanbiedingen) per chase card en box; `ratio EU`, EU/US-spread; kalibratierapport `docs/calibration-<dag>.md` (workflow `calibrate`). **Uitkomst kalibratie 19-10-2026**: CardTrader-vraagprijzen liggen mediaan 15–35% boven TCGplayer-markt en zijn dun voor de meeste hits → waardering blijft TCGplayer (verkoopgebaseerd) in €; CardTrader levert de EU-boxprijs (EU-verkopers) en per-kaart 'wat je nu betaalt'. Cardmarket-trend als tweede waarderingsbron: steekproef gepland.
- **Fase 4** — PWA op de telefoon met push.
- **Fase 5** — slabs: graded prijzen (PSA 9/10) naast raw → grading-gap-alerts.
