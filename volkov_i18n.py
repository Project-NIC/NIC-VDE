"""Tiny translation layer for Volkov Data's UI strings.

Switching language is exactly what you'd expect: pick a different word list.
``tr(text)`` looks the English source string up in the active language's table
and falls back to the English text itself when there's no entry — so an
untranslated string still shows up readable, just in English.

Add a language by dropping another code → dict into ``TABLES`` below. Keys are
the English UI strings as written in the code; values are the translations.
"""
from __future__ import annotations

# Languages offered in Options → Language (code, native name).
LANGUAGES = [
    ("en", "English"),
    ("cs", "Čeština"),
    ("fr", "Français"),
    ("es", "Español"),
    ("ru", "Русский"),
]

# code → { english_source: translation }. English is empty (identity fallback).
TABLES: dict[str, dict[str, str]] = {
    "en": {},
    "cs": {
        # menu bar titles
        "Left": "Levý", "Files": "Soubory", "Commands": "Příkazy",
        "Options": "Volby", "Right": "Pravý",
        # Files menu
        "Info": "Informace", "Repair / check": "Oprava / kontrola",
        "View": "Zobrazit", "Values": "Hodnoty",
        "Edit schema / stations": "Upravit schéma / stanice",
        "Copy": "Kopírovat",
        "Rename or move": "Přejmenovat/přesunout",
        "Export to SQL": "Export do SQL", "Make directory": "Vytvořit složku",
        "Delete": "Smazat", "Quit": "Konec",
        # Commands menu
        "Swap panels": "Prohodit panely", "Re-read both": "Načíst oba znovu",
        # sort
        "Name": "Název", "Extension": "Přípona", "Time": "Čas", "Size": "Velikost",
        "Unsorted": "Netříděno", "Sequence": "Pořadí", "Reverse": "Obráceně",
        "Re-read": "Načíst znovu",
        # Options menu
        "Language": "Jazyk", "Export raw values": "Exportovat syrová data",
        "Save setup": "Uložit nastavení",
    },
    "fr": {
        "Left": "Gauche", "Files": "Fichiers", "Commands": "Commandes",
        "Options": "Options", "Right": "Droite",
        "Info": "Info", "Repair / check": "Réparer / vérifier",
        "View": "Voir", "Values": "Valeurs",
        "Edit schema / stations": "Éditer schéma / stations",
        "Copy": "Copier",
        "Rename or move": "Renommer/déplacer",
        "Export to SQL": "Exporter en SQL", "Make directory": "Créer un dossier",
        "Delete": "Supprimer", "Quit": "Quitter",
        "Swap panels": "Inverser les panneaux", "Re-read both": "Recharger les deux",
        "Name": "Nom", "Extension": "Extension", "Time": "Heure", "Size": "Taille",
        "Unsorted": "Non trié", "Sequence": "Séquence", "Reverse": "Inverse",
        "Re-read": "Recharger",
        "Language": "Langue", "Export raw values": "Exporter valeurs brutes",
        "Save setup": "Enregistrer la config",
    },
    "es": {
        "Left": "Izquierda", "Files": "Archivos", "Commands": "Comandos",
        "Options": "Opciones", "Right": "Derecha",
        "Info": "Info", "Repair / check": "Reparar / comprobar",
        "View": "Ver", "Values": "Valores",
        "Edit schema / stations": "Editar esquema / estaciones",
        "Copy": "Copiar",
        "Rename or move": "Renombrar/mover",
        "Export to SQL": "Exportar a SQL", "Make directory": "Crear carpeta",
        "Delete": "Borrar", "Quit": "Salir",
        "Swap panels": "Intercambiar paneles", "Re-read both": "Recargar ambos",
        "Name": "Nombre", "Extension": "Extensión", "Time": "Hora", "Size": "Tamaño",
        "Unsorted": "Sin ordenar", "Sequence": "Secuencia", "Reverse": "Inverso",
        "Re-read": "Recargar",
        "Language": "Idioma", "Export raw values": "Exportar valores crudos",
        "Save setup": "Guardar configuración",
    },
    "ru": {
        "Left": "Левая", "Files": "Файлы", "Commands": "Команды",
        "Options": "Настройки", "Right": "Правая",
        "Info": "Инфо", "Repair / check": "Проверка / ремонт",
        "View": "Просмотр", "Values": "Значения",
        "Edit schema / stations": "Править схему / станции",
        "Copy": "Копировать",
        "Rename or move": "Переименовать/переместить",
        "Export to SQL": "Экспорт в SQL", "Make directory": "Создать каталог",
        "Delete": "Удалить", "Quit": "Выход",
        "Swap panels": "Поменять панели", "Re-read both": "Перечитать обе",
        "Name": "Имя", "Extension": "Расширение", "Time": "Время", "Size": "Размер",
        "Unsorted": "Без сортировки", "Sequence": "Порядок", "Reverse": "Обратно",
        "Re-read": "Перечитать",
        "Language": "Язык", "Export raw values": "Экспорт сырых значений",
        "Save setup": "Сохранить настройки",
    },
}


class Translator:
    """Holds the active language and translates UI strings."""

    def __init__(self, lang: str = "en"):
        self.set_lang(lang)

    def set_lang(self, lang: str) -> None:
        self.lang = lang if lang in TABLES else "en"
        self._table = TABLES[self.lang]

    def tr(self, text: str) -> str:
        return self._table.get(text, text)
