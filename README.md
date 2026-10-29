# TCGWatcher

Dagelijkse prijs-intelligence voor sealed TCG-product op de Engelse markt. Nu: Dragon Ball Super Card Game
**Fusion World**; Bandai's **NARUTO CARD GAME** (launch juni 2027) wordt bewaakt op aankondigingen en pre-orders.

**Wat het doet, elke avond (21:30 UTC):**

1. **fetch** — haalt de dagstand van [tcgcsv.com](https://tcgcsv.com) (TCGplayer-catalogus en -prijzen) op en slaat die **ruw** op.
2. **validate** — kwaliteitspoort: schema, duplicaten, uitschieters, dekking, dag-op-dag-verschillen. Bij `fail` wordt er niets gepubliceerd.
3. **build** — Chase Score per set, prijstrends (7d/30d), `site/data/latest.json` met herkomst op elk blok.
4. **alerts** — alleen wat je nu kunt doen: 🎯 **KOOP** (hits € ÷ goedkoopste EU-aanbod ≥ 0,80), 🆕 nieuwe productlijn (= pre-ordermoment), ⚠️ alleen bij FAIL. Weekbriefing op zondag.
5. **retail** (ook elke 2 uur via `retail.yml`) — EU-webshops uit `config/retailers.yaml`: prijs, voorraad en pre-orderstatus per sealed product (Engelstalig; JP-listings worden genegeerd). **Alleen shops met een eigen API (Shopify / WooCommerce Store API) tellen** voor meldingen, de pagina en de beste koop; HTML-shops leveren alleen data (tekstheuristiek is onbetrouwbaar op dode pre-orderpagina's). Melding alleen als een box/case **koopbaar wordt** (op voorraad / pre-order open) én koopwaardig is (ratio ≥ 0,70) of de set nog niet uit is. Uitverkocht, prijsstijgingen en nieuwe listings blijven in `data/retail/`.

De pagina (`site/`, GitHub Pages) heeft zes tabbladen: **Vandaag** (KPI's: koopsignalen, top 5 slabs, portfolio ingekocht/waarde nu, controlegroep, laatste aankondiging; daaronder de koopwaardige boxen en de top 5 slabs als kaarten met foto en shopknoppen — hetzelfde als de Telegram-berichten), **Boxen** (ranking op EU-ratio met detail: samenstelling, prijsvergelijking per bron, retail, alle chase cards), **Slabs** (alle rendabele kandidaten + graded aanbod in de EU), **Portfolio** (paper-posities met waarde nu en P&L per soort), **Nieuws & retail** (Bandai-aankondigingen, koopbaar bij betrouwbare shops) en **Instellingen** (aannames, drempel, koers, uitleg).

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
| `data/cardmarket/<dag>.json` | Cardmarket **officiële prijsgids** (CDN, publiek): per chase card `low`/`trend`/`avg7`/`avg30` (gekoppeld via CardTrader `card_market_ids`), per set de box | nooit |
| `data/state/*.json` | sync-stand, bekende groepen, actieve alert-episodes, laatste retailer-status | dagelijks |
| `site/data/latest.json` | wat de pagina leest; alles herberekenbaar uit `data/` | dagelijks |

Canonieke sleutel per kaart: `GAME:SET:NUMMER:VARIANT` (bijv. `dbfw:FB10:FB10-121:SAA`), naast het
TCGplayer `productId`. Andere bronnen (Cardmarket, eBay, retailers) koppelen later op die sleutel.

## Een game toevoegen

Eén blok in `config/games.yaml`: TCGCSV-categorie, box-samenstelling, hit-klassen (regels + schatting per box),
variant-suffixen, set-overrides, en de vlaggen `eu_sources` (CardTrader/eBay koppelen), `alerts` (Telegram aan/uit) en
`ebay_sets` (hoeveel recente sets slab-zoekopdrachten krijgen). Geen code nodig.

**Masters (`dbs`, TCGplayer-categorie 27, BT-sets)** is op 19-10-2026 toegevoegd in de **stille stand**: wel pagina, CardTrader/eBay
en paper-portfolio (koopregel + controlegroep), géén Telegram-koopsignalen tot het eerste maandrapport laat zien dat de ratio's kloppen.
Hit-klassen: God Rare (~1/case), Secret Rare (~1/2 boxen), Special Leader Rare (~1/12 packs), Special Rare (~1/6 packs), Concept Rare
(aanname 1/box), Super Rare. Schattingen uit community-openingen, niet gekalibreerd.

## Lokaal draaien

```bash
pip install -r requirements.txt
python -m tcgwatcher all --no-send          # fetch → validate → build → alerts (zonder Telegram)
python -m tcgwatcher build --day 2026-10-15 # alleen herbouwen uit opgeslagen data
python -m pytest -q
cd site && python -m http.server 8000       # http://localhost:8000
```

## Telegram

Berichten (v3, `config/alerts.yaml`):

| Bericht | Wanneer | Regel |
|---|---|---|
| 🎯 **KOOP** | set wordt koopwaardig | `EV€ hits (excl. ★★) ÷ goedkoopste EU-aanbod ≥ buy.threshold` (0,80); alleen bij **≥ 3 EU-listings** over CardTrader + eBay EU + retailers samen (`eu_landed.min_offers`), anders vlag `thin` en geen signaal. Eén kaart per episode; opnieuw alleen als de beste prijs ≥10% daalt. Goedkoopste aanbod = **wat je werkelijk betaalt**: CardTrader Zero-prijs + vaste Zero-kosten (`eu_landed.zero_fee_eur`), anders goedkoopste EU-verkoper + geschatte verzending (`eu_landed.seller_shipping_eur`), of een webshop met voorraad/pre-order. Knop linkt direct naar het product. |
| 🟡 PRE-ORDER OPEN / ✅ OP VOORRAAD / 📉 GOEDKOPER | box/case wordt koopbaar bij een betrouwbare webshop | alleen als ratio tegen díe prijs ≥ `buy.near` (0,70), of de set nog niet gewaardeerd is (nieuwe set) |
| 🍥 NARUTO · … | **elk** Naruto-product (deck, pack, box, case) dat koopbaar wordt bij een betrouwbare webshop | geen ratio-drempel: launch-lineup, als eerste bij zijn |
| 🆕 productlijn | nieuwe set in de TCGplayer-catalogus | eerste pre-ordersignaal |
| 📰 officiële aankondiging | nieuw product/set op de Bandai-pagina's (Fusion World news + productlijst, NARUTO CARD GAME news; `config/news.yaml`, elke 2 uur) | alleen product-/release-nieuws; app-berichten en events worden wel opgeslagen (`data/news/`) maar niet gestuurd |
| 🏆 **SLAB #1–#5** | per kaart één bericht met foto (max 5 per dag, opnieuw bij ≥25% beter netto of ≥10% lagere raw): kaarten die raw goedkoop zijn en graded veel waard; raw-prijs = goedkoopste van CardTrader (Zero) en eBay EU-verkopers, all-in; TCGplayer alleen als referentie — per service (PSA/BGS/CGC) `Σ aandeel×mediaan graded-prijs` × 0,87 − servicekosten − raw tegen de **werkelijke** inkoopprijs (CardTrader EU-ask + €5); staat er in de EU een graded exemplaar ≤ 1,15× raw te koop (eBay DE/NL of CardTrader graded), dan staat dat op dezelfde regel. **Eén knop per product** → direct naar de shop. | `grading` en `slabs` in `config/alerts.yaml`; eBay Browse API + CardTrader graded, dagelijks; verkopers laten 'alt art' vaak weg of noemen een gewone kaart 'God Rare' → **fotocheck**: de listing-foto wordt op kleurpalet vergeleken met de officiële afbeelding van elke variant van dat nummer en moet het meest op de geclaimde variant lijken; daarnaast gelden listings > 4× de laagste van dezelfde grade als verkeerd gelabeld |
| 📋 briefing | uit (`briefing: off`); `weekly`/`daily` om aan te zetten | ranking op EU-ratio, bewegingen, boxen, retail, Naruto |
| ⚠️ datakwaliteit | alleen bij FAIL (pagina niet bijgewerkt) | `quality_issues: fail_only` |

Stuur `/mute FB10`, `/unmute FB10`, `/mute all` of `/status` naar de bot; wordt bij de volgende run verwerkt.


Repo-secrets `TELEGRAM_BOT_TOKEN` (van @BotFather) en `TELEGRAM_CHAT_ID`. Zonder token draait alles, maar
alerts blijven in `data/alerts/<dag>.json`. Zonder chat-id print de workflow-log de kandidaat-chat-id's nadat je
`/start` naar de bot hebt gestuurd.

## Paper-portfolio (controle van de signalen)

Elke melding wordt automatisch een fictieve aankoop in `data/paper/positions.json`: 🎯 KOOP = één box tegen de all-in EU-prijs
(+ EV op dat moment), 📈 grade-kans top 5 = de **raw** kaart tegen de goedkoopste CardTrader EU-ask + €5 verzending (wat je betaalt; TCGplayer-market als terugval, geen grading verondersteld), 🏆 slab-koop top 5 =
de graded kaart tegen de EU-listingprijs. Daarnaast per maand een **controlegroep**: elke gescoorde set zónder koopsignaal, één box
tegen de all-in prijs. Op de 1e van de maand (`python -m tcgwatcher paper --report` voor tussendoor): `docs/paper-<jaar-maand>.md` +
één Telegram-bericht met P&L per soort (zelfde maatstaf als de aankoop), winnaars, EV toen → nu, en koopsignalen vs controlegroep.

## Roadmap

- **Fase 2** — alerts live, drempels tunen op echte verkopen (FB11 is net uit).
- **Fase 3a** — retailer-monitor (gedaan; shops bijvoegen = regel in `config/retailers.yaml`).
- **Fase 3b** — CardTrader EU-prijzen (officiële API, EN/EUR, laagste echte aanbiedingen) per chase card en box; `ratio EU`, EU/US-spread; kalibratierapport `docs/calibration-<dag>.md` (workflow `calibrate`). **Uitkomst kalibratie 19-10-2026**: CardTrader-vraagprijzen liggen mediaan 15–35% boven TCGplayer-markt en zijn dun voor de meeste hits → waardering blijft TCGplayer (verkoopgebaseerd) in €; CardTrader levert de EU-boxprijs (EU-verkopers) en per-kaart 'wat je nu betaalt'. Cardmarket-steekproef 19-10-2026 (`docs/calibration-cardmarket-2026-10-19.md`): EU-verkopen van alt-arts ≈1,2× TCGplayer, Cardmarket-boxen ≈0,88× CardTrader Zero (vóór verzending). Sinds 19-10 dagelijks via de **officiële Cardmarket-prijsgids** (geen API-key, geen bot-wall): laagste aanbod + 30d-verkopen per kaart en box; Cardmarket-box = koopoptie (laagste aanbod + €18), `ev_cm_excl_saa` = EV op EU-verkopen als tweede waardering.
- **Fase 4** — PWA op de telefoon met push.
- **Fase 5** — slabs (gedaan): eBay Browse API (EBAY_DE/EBAY_NL) voor de top-6 hits per set → laagste PSA/BGS/CGC 10/9.5/9 naast raw; 🏆-alert bij graded ≤ 1,15× raw. Pagina-sectie "Slabs".


### Slab-rotatie
Telegram krijgt per run maximaal 5 slab-kaarten die **nog niet eerder gestuurd** zijn (`data/state/alerts.json → sent_slabs`, wordt nooit geleegd). Een al gemelde kaart komt alleen terug als hij duidelijk beter werd (netto ≥ +25% of raw ≥ 10% goedkoper). De radar toont alle kandidaten, met een 📨-badge bij de al gemelde.


### Logica-review
Zie `docs/review-2026-10-19.md` voor de onafhankelijke doorloop van alle regels en wat daaruit is gefixt (paper-portfolio op dezelfde bron/waardering als de aankoop, status alleen bij afgeleverde berichten, JP-locaties uit slabs, EU = EU27+CH+NO).


### Als app op je telefoon (PWA)
Open https://np7editz-bot.github.io/TCGWatcher/ in Safari (iPhone) of Chrome (Android) → Deel / menu → **Zet op beginscherm**. De radar opent dan als app (eigen icoon, volledig scherm); de laatst geladen data blijft offline zichtbaar, en bij elke opening wordt `latest.json` opnieuw gehaald. Bestanden: `site/manifest.webmanifest`, `site/sw.js`, `site/icons/`.


### Bronnen per product (sinds 2026-10-20)
Boxen: CardTrader (Zero), Cardmarket (prijsgids; bij Masters alleen indicatie) en de vertrouwde webshops. eBay telt niet mee voor boxen (bulk-, rip&ship- en Japanse 'boxen' bleken niet betrouwbaar te filteren) — eBay is de bron voor slabs en raw kaarten. Schakelaar: `eu_landed.ebay_boxes` in `config/alerts.yaml`.


### Vaste-inhoud producten (anniversary sets, promo packs)
Sealed producten zonder loterij — '1st/2nd Anniversary Set Bundle', '2nd Anniversary Pack' — staan op de Boxen-pagina onder *Vaste inhoud*: prijs (TCGplayer, CardTrader, Cardmarket) naast de som van de kaarten erin (gevonden via de naam-suffix '(2nd Anniversary Set)'). Alleen ter info: geen Telegram-meldingen, geen paper-posities. Detectie: `build.fixed_content_bundles`.


### Waardering (sinds 2026-10-21): EU-bronnen eerst
Elke kaart: Cardmarket-verkopen (avg30 → avg7 → trend) → CardTrader-laagste ask × 0,83 → TCGplayer × koers alleen als geen EU-bron de kaart prijst. Geen set-brede terugval meer; `valuation` = `cardmarket` (≥ 50% van de hits op Cardmarket-verkopen), `eu-mixed` (EU-keten, grotendeels CardTrader) of `tcgplayer` (geen EU-data). Geldt voor box-EV, slab-raw-waarde en vaste-inhoud producten; de US-EV blijft als referentie.


## Riftbound (sinds 2026-10-23)
Derde spel in de radar: `riftbound` in `config/games.yaml` (TCGplayer-categorie 89; CardTrader-spel 22; Cardmarket-spel 22).
Boostersets OGN · SFD · UNL · VEN · RAD · LGC; sealed heet bij TCGplayer 'Booster Display'. Hit-klassen uit de rarity 'Showcase'
(Alternate Art, Overnumbered, Signature ★, Ultimate) plus Epic en Rare; pull-rates uit community-openingen, lage zekerheid.
Meldingen (KOOP, SLAB) staan aan; het spel zit **niet** in de paper-portfolio (`paper: false`).
Nog open: Cardmarket-kaartlinks (zoeklink tot het URL-patroon per klik bevestigd is), retailers (Riftbound-categorie-URL's per shop),
Riot-nieuwspagina (Next.js; voorlopig dekt de CardTrader-expansiewatch nieuwe sets maanden vooruit).
Verkenning van een nieuw spel: `python -m tcgwatcher probe --category <id> --slug <naam>` (workflow `probe`).


### Nieuwsberichten (sinds 2026-10-29)
Elk nieuw item op een officiële pagina wordt één 📰-bericht, net als KOOP/SLAB: Fusion World (nieuws + productlijst, alleen producttitels), Naruto (alles — pre-launch), Riftbound (Riot-nieuws, uit de inline JSON van de pagina). Daarnaast: nieuwe TCGplayer-productlijnen (🆕) en nieuwe CardTrader-expansies (📰, maanden vooruit). Eerste run per bron is stil.
