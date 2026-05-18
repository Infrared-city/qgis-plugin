from enum import Enum, unique


@unique
class TreeType(str, Enum):
    STONE_PINE = "Stone Pine"
    HOLM_OAK = "Holm Oak"
    PENCIL_TREE = "Pencil Tree"
    MEDITERRANEAN_CYPRESS = "Mediterranean Cypress"
    EUROPEAN_LARCH = "European Larch"
    ENGLISH_OAK = "English Oak"
    BUSHWILLOW = "Bushwillow"
    BALD_CYPRESS = "Bald Cypress"


    def __str__(self) -> str:
        return self.value

@unique
class TreeSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

    def __str__(self) -> str:
        return self.value
