"""Ecomorphology reference from the supplied Annotatiathon head-motion workbook.

The 16 groups are operational project strata, not an established phylogenetic
taxonomy. Known folders are mapped using the workbook's 72 source folder
inventory. A folder does not prove usable footage or observed movements.
"""
import re

GROUPS = (
    "General perching birds",
    "Parrots and parakeets",
    "Trunk-climbing birds",
    "Large-billed specialists",
    "Hummingbird-type birds",
    "Aerial specialists",
    "Diurnal raptors",
    "Owls",
    "Terrestrial ground birds",
    "Large flightless runners",
    "Surface-swimming waterfowl",
    "Aquatic diving birds",
    "Penguins",
    "Long-legged waders",
    "Shorebirds",
    "Long-winged seabirds",
)

MOVEMENT_TYPES = (
    ("head_bobbing", "Head bobbing / thrust"),
    ("rapid_head_turn", "Rapid head turns / saccades"),
    ("pecking", "Pecking / drilling"),
    ("prey_strike", "Prey capture / striking"),
    ("gaze_tracking", "Gaze stabilization / tracking"),
    ("preening", "Preening / grooming"),
    ("feeding", "Feeding / bill manipulation"),
    ("courtship", "Courtship / display"),
    ("other", "Other observed head movement"),
    ("none", "No clear head movement"),
)
MOVEMENT_LABELS = dict(MOVEMENT_TYPES)

# source_folder | common species name | workbook ecomorphology group
SOURCE_FOLDERS = """
ActitisHypoleucos|Common Sandpiper|Shorebirds
AlcaTorda|Razorbill|Aquatic diving birds
AlcedoAtthis|Common Kingfisher|Large-billed specialists
AnasUndulata|Yellow-billed Duck|Surface-swimming waterfowl
ApusApus|Common Swift|Aerial specialists
AquilaChrysaetos|Golden Eagle|Diurnal raptors
AraMacao|Scarlet Macaw|Parrots and parakeets
ArdeaCinerea|Grey Heron|Long-legged waders
AsioOtus|Long-eared Owl|Owls
AtheneCunicularia|Burrowing Owl|Owls
AythyaFuligula|Tufted Duck|Aquatic diving birds
BalaenicepsRex|Shoebill|Long-legged waders
BangsiaArcaei|Blue-and-gold Tanager|General perching birds
ButeoButeo|Common Buzzard|Diurnal raptors
CaprimulgusEuropaeus|European Nightjar|Aerial specialists
CasuariusCasuarius|Southern Cassowary|Large flightless runners
CiconiaCiconia|White Stork|Long-legged waders
CiconiaNigra|Black Stork|Long-legged waders
CircusCyaneus|Hen Harrier|Diurnal raptors
ColumbaPalumbus|Common Wood Pigeon|General perching birds
CoraciasGarrulus|European Roller|General perching birds
CorvusCorax|Common Raven|General perching birds
CorvusCorone|Carrion Crow|General perching birds
CoturnixCoturnix|Common Quail|Terrestrial ground birds
CreagrusFurcatus|Swallow-tailed Gull|Long-winged seabirds
CuculusCanorus|Common Cuckoo|General perching birds
CygnusOlor|Mute Swan|Surface-swimming waterfowl
DendrocoptesMedius|Middle Spotted Woodpecker|Trunk-climbing birds
DicrurusParadiseus|Greater Racket-tailed Drongo|General perching birds
DiomedeaExulans|Wandering Albatross|Long-winged seabirds
DromaiusNovaehollandiae|Emu|Large flightless runners
FalcoSubbuteo|Eurasian Hobby|Diurnal raptors
FregataMagnificens|Magnificent Frigatebird|Long-winged seabirds
FulmarusGlacialis|Northern Fulmar|Long-winged seabirds
GallusGallus|Red Junglefowl|Terrestrial ground birds
GaviaImmer|Common Loon|Aquatic diving birds
GelochelidonNilotica|Gull-billed Tern|Long-winged seabirds
GrusGrus|Common Crane|Long-legged waders
HaematopusOstralegus|Eurasian Oystercatcher|Shorebirds
HirundoRustica|Barn Swallow|Aerial specialists
LarusArgentatus|Herring Gull|Long-winged seabirds
LophuraDiardi|Siamese Fireback|Terrestrial ground birds
LusciniaMegarhynchos|Common Nightingale|General perching birds
MeleagrisGallopavo|Wild Turkey|Terrestrial ground birds
MelopsittacusUndulatus|Budgerigar|Parrots and parakeets
OpisthocomusHoazin|Hoatzin|Terrestrial ground birds
PandionHaliaetus|Osprey|Diurnal raptors
PasserDomesticus|House Sparrow|General perching birds
PelagodromaMarina|White-faced Storm Petrel|Long-winged seabirds
PhaethonLepturus|White-tailed Tropicbird|Long-winged seabirds
PhalacrocoraxHarrisi|Flightless Cormorant|Aquatic diving birds
PhasianusColchicus|Ring-necked Pheasant|Terrestrial ground birds
PhoenicopterusRuber|American Flamingo|Long-legged waders
PodargusStrigoides|Tawny Frogmouth|Aerial specialists
PodicepsCristatus|Great Crested Grebe|Aquatic diving birds
PsittacusErithacus|African Grey Parrot|Parrots and parakeets
PteroglossusBailloni|Saffron Toucanet|Large-billed specialists
RamphastosDicolorus|Red-breasted Toucan|Large-billed specialists
RegulusRegulus|Goldcrest|General perching birds
RhynchotusRufescens|Red-winged Tinamou|Terrestrial ground birds
SagittariusSerpentarius|Secretarybird|Diurnal raptors
ScolopaxRusticola|Eurasian Woodcock|Shorebirds
SelasphorusRufus|Rufous Hummingbird|Hummingbird-type birds
SpheniscusDemersus|African Penguin|Penguins
SteatornisCaripensis|Oilbird|Aerial specialists
StruthioCamelus|Common Ostrich|Large flightless runners
TachyeresBrachypterus|Falkland Steamer Duck|Surface-swimming waterfowl
TaeniopygiaGuttata|Zebra Finch|General perching birds
ThaluraniaFurcata|Fork-tailed Woodnymph|Hummingbird-type birds
TurdusMerula|Eurasian Blackbird|General perching birds
TytoAlba|Barn Owl|Owls
VulturGryphus|Andean Condor|Diurnal raptors
"""


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


FOLDER_LOOKUP = {
    normalized(row.split("|")[0]): tuple(row.split("|")[1:])
    for row in SOURCE_FOLDERS.splitlines() if row.strip()
}
GROUP_LOOKUP = {normalized(group): group for group in GROUPS}


def suggested_classification(source_folder: str) -> tuple[str | None, str | None]:
    """Best-effort source-folder suggestion, never a claim about video movement."""
    match = FOLDER_LOOKUP.get(normalized(source_folder))
    if match:
        species, group = match
        return group, species
    return GROUP_LOOKUP.get(normalized(source_folder)), None
