"""Recipe-level substitutions: swap an ingredient for one that does the same job in the dish.

Unlike the jury's same-category SKU swap, a rule changes the ingredient and, where needed, the
amount and unit, and says what changes on the plate. The substitute is then sourced and checked
like any other ingredient, so allergen and claim rules still apply.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Swap:
    intent: str
    ratio: float  # substitute quantity per unit of the original
    note: str
    unit: str | None = None  # None: same unit as the original


# Ordered: first matching keyword wins, so specific phrases sit above general ones
# ("sour cream" before "cream", "buttermilk" before "butter").
RULES: list[tuple[tuple[str, ...], list[Swap]]] = [
    (("buttermilk", "piimä"), [
        Swap("turkish yoghurt", 0.75, "Use 3 parts Turkish yoghurt thinned with 1 part water or milk; same tang and acidity for batters and marinades.", "kg"),
    ]),
    (("sour cream", "smetana", "kermaviili"), [
        Swap("creme fraiche", 1.0, "Crème fraîche 1:1; richer and does not split when heated."),
        Swap("turkish yoghurt", 1.0, "Turkish yoghurt 1:1; lighter, add off the heat so it does not split."),
    ]),
    (("creme fraiche", "crème fraîche", "ranskankerma"), [
        Swap("sour cream", 1.0, "Sour cream 1:1; add at the end, it can split if boiled."),
    ]),
    (("cooking cream", "ruokakerma"), [
        Swap("whipping cream", 1.0, "Whipping cream 1:1; richer, so reduce the sauce a little less."),
        Swap("creme fraiche", 1.0, "Crème fraîche 1:1 in sauces and soups; slightly tangy."),
    ]),
    (("whipping cream", "double cream", "heavy cream", "kuohukerma", "cream"), [
        Swap("cooking cream", 1.0, "Cooking cream 1:1 for sauces and soups; it will not whip, so set desserts with gelatine instead."),
        Swap("creme fraiche", 1.0, "Crème fraîche 1:1 in sauces; for desserts it gives a softer, tangier set."),
    ]),
    (("mascarpone",), [
        Swap("cream cheese", 1.0, "Cream cheese 1:1 loosened with a splash of cream; slightly tangier, sets the same."),
    ]),
    (("cream cheese", "tuorejuusto"), [
        Swap("mascarpone", 1.0, "Mascarpone 1:1; richer and less tangy, add lemon if needed."),
    ]),
    (("quark", "rahka"), [
        Swap("turkish yoghurt", 1.0, "Turkish yoghurt 1:1; softer, so drain it briefly for fillings."),
    ]),
    (("greek yoghurt", "turkish yoghurt", "yoghurt", "jogurtti"), [
        Swap("creme fraiche", 1.0, "Crème fraîche 1:1; richer, keep it cold for dressings."),
    ]),
    (("butter",), [
        Swap("margarine", 1.0, "Baking margarine 1:1 by weight; fine for sauces and baking, less flavour in finishing."),
        Swap("rapeseed oil", 0.8, "Use 0.8 L rapeseed oil per kg of butter for sautéing and sponges; not for laminated pastry.", "L"),
    ]),
    (("milk", "maito"), [
        Swap("oat drink", 1.0, "Oat drink 1:1 in sauces and bakes; slightly sweeter, not for clean milk flavours."),
    ]),
    (("parmesan",), [
        Swap("grana padano", 1.0, "Grana Padano 1:1; milder and a little less salty."),
    ]),
    (("emmental", "edam", "gouda", "grated cheese", "juusto"), [
        Swap("edam", 1.0, "Edam 1:1 for melting and gratins; milder flavour."),
        Swap("emmental", 1.0, "Emmental 1:1 for melting and gratins; nuttier flavour."),
    ]),
    (("salmon", "lohi"), [
        Swap("rainbow trout", 1.0, "Rainbow trout 1:1; similar fat and colour, shorten cooking by a minute or two."),
    ]),
    (("chicken breast", "chicken fillet", "broilerin rintafilee"), [
        Swap("chicken thigh", 1.0, "Chicken thigh 1:1; juicier, needs a few minutes longer."),
    ]),
]


def swaps_for(intent: str) -> list[Swap]:
    """Recipe substitutes for an ingredient, best first; [] when no rule applies."""
    text = intent.casefold()
    for keywords, swaps in RULES:
        if any(k in text for k in keywords):
            # never "substitute" an ingredient with itself (e.g. Turkish yoghurt under the yoghurt rule)
            return [s for s in swaps if s.intent not in text]
    return []
