# Cardmarket-steekproef — 19 oktober 2026

Handmatige steekproef in de browser (Cloudflare blokkeert geautomatiseerd verkeer; na ~45 verzoeken op 1 per 4 s opnieuw 403).
13 Fusion World-boxen en 26 chase cards (FB08–FB11, V.2 = Alternate Art, V.3 = Super Alternate Art / "God Rare").
Ruwe data: `data/cardmarket/sample-2026-10-19.json`. Vergeleken met TCGplayer market (×ECB 0,8887) en CardTrader Zero.

## Boxen (wat je in de EU betaalt)

| | mediaan |
|---|---|
| Cardmarket 30d-verkoopgemiddelde ÷ TCGplayer market (€) | **0,66** |
| Cardmarket laagste aanbod ÷ CardTrader Zero laagste aanbod | **0,88** |

Cardmarket's laagste aanbod ligt ~12% onder de CardTrader Zero-prijs, maar daar komt bij Cardmarket €15–20 verzending bij (FB09 €159 + €20 ≈ €179 vs CT Zero all-in €187; FB10 €190 vs CT €168).
**Conclusie: CardTrader Zero all-in is een eerlijke EU-prijsproxy (±10%)**; waar Cardmarket duidelijk goedkoper is, is dat per set zichtbaar op de pagina. Opvallend: FB09 Cardmarket 30d-gemiddelde €260 maar laagste aanbod €159 — de prijs zakt hard (89 aanbiedingen).

## Chase cards (waarde van de hits)

| versie | Cardmarket 30d-verkopen ÷ TCGplayer market | laagste aanbod ÷ TCGplayer | n |
|---|---|---|---|
| V.2 Alternate Art | **1,22** | 1,03 | 13 |
| V.3 Super Alternate Art | 0,90 (dun: 4–20 stuks) | 1,07 | 8 |

Europese verkoopprijzen van alt-arts liggen ~20% **boven** TCGplayer market; de laagste asks zijn gelijk aan TCGplayer. Onze EV (TCGplayer × koers) is dus eerder conservatief dan te optimistisch voor de EU — goed voor een koopsignaal.
Bij FB11 (3 dagen uit) heeft Cardmarket nog geen 30d-verkopen voor de ★★; TCGplayer heeft daar alleen listingprijzen (`mid`). Beide bronnen zijn in de eerste week onbetrouwbaar → `low_coverage` blijft terecht uitgesloten.

## Besluit
- Waardering blijft **TCGplayer market × ECB** (verkoopgebaseerd, dagelijks, volledig). Geen correctiefactor: de EU-premie van ~1,2 op alt-arts is een marge aan de veilige kant.
- EU-boxprijs blijft **CardTrader Zero + €6** (API, dagelijks). Cardmarket is geen dagelijkse bron (geen API, bot-wall); een handmatige steekproef per kwartaal volstaat om de verhouding (0,88) te bewaken.
- Vorige kalibratie (CardTrader-vraagprijzen 1,12–1,39× TCGplayer, `docs/calibration-2026-10-18.md`) blijft staan: CardTrader-asks zijn geen waardering.
