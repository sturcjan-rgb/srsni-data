# Fotky hráčů pro Highlights 2

Hotová pozadí **1080×1920** (pozadí, pruhy, stín i hráč), na ně se kreslí jen texty.
Texty jsou v levém sloupci, hráč má být vpravo.

Který soubor patří kterému hráči, určuje **`hraci.json`**. Klíč je jméno-příjmení bez diakritiky
(tak, jak je ve FIBA soupisce), hodnota název souboru. Nový hráč = nahrát obrázek a přidat řádek.

Hráč, který v `hraci.json` není, se hledá podle názvu souboru v tomto pořadí:
`jmeno-prijmeni.png` → `prijmeni-inicial.png` (např. `svoboda-m.png`) → `prijmeni.png`
(samotné příjmení jen když je v soupisce jediné, takže se Svobodové ani Zvolánkové nepletou).

Chybí: Jakub Zvolánek (`zvolanek-j.png`), Marek Zvolánek (`zvolanek-m.png`),
František Suchánek (`suchanek.png`). Bez fotky se kreslí výchozí tmavé pozadí s pruhy.

`sykora.png` je ukázka i s texty — jako pozadí se nepoužívá (Sýkora má `sykora-1.png`).
