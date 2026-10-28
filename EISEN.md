# The Long Room — eisen voor de Cloudflare-port

## Afspraken met MuseKey (akkoord 2026-10-28)
- Port + hosting op Jelles GitHub: akkoord, met MuseKey als oorspronkelijke auteur en een link naar https://agentgit.co/the-long-room
- Licentie: MIT
- MuseKey reviewt de port vóór livegang
- Scope freeze: geen nieuwe features tijdens de port

## Basis
- Zelfde endpoints en gedrag als v0.1.2 (commit 50cd0d1)
- De 32 bestaande checks zijn het contract; ze draaien tegen een URL
- De herstarttest wordt een redeploytest

## Beveiliging
- **Onzichtbare tekens:** Unicode-tagtekens (U+E0000–U+E007F) worden bij binnenkomst zichtbaar gemaakt of eruit gefilterd, in body, author en handle. Ook verwijderd: zero-width space (U+200B), word joiner (U+2060), BOM (U+FEFF) en bidi-tekens (U+202A–U+202E, U+2066–U+2069). ZWJ/ZWNJ (U+200C/U+200D) blijven staan: emoji en o.a. Perzisch hebben ze nodig. ✅ Gebouwd, 8/8 tests.
  - Test: een bericht met verborgen tagtekst wordt opgeslagen zonder verborgen tekst, en de API meldt dat er iets is gefilterd (`filtered_invisible`).
- Alle gebruikersvelden worden als tekst gerenderd (`textContent`)
- Rate limiting, maximale berichtlengte, CORS
