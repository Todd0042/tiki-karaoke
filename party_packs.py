"""
Tiki Karaoke - Pre-Party Starter Packs
Curated song collections for offline karaoke parties (camping, tailgates, cabins).
All songs are dynamically resolved using search.py filter engines (Karaoke, Sing-Along, Music Video).
"""
from typing import Dict, List, Any

PARTY_PACKS = [
    {
        "id": "essentials",
        "name": "🌴 Tiki Party Essentials",
        "description": "20 of the most iconic, crowd-pleasing karaoke anthems of all time.",
        "icon": "🌴",
        "track_count": 20,
        "tracks": [
            {"title": "Don't Stop Believin'", "artist": "Journey"},
            {"title": "Sweet Caroline", "artist": "Neil Diamond"},
            {"title": "Bohemian Rhapsody", "artist": "Queen"},
            {"title": "I Want It That Way", "artist": "Backstreet Boys"},
            {"title": "Mr. Brightside", "artist": "The Killers"},
            {"title": "Livin' on a Prayer", "artist": "Bon Jovi"},
            {"title": "Dancing Queen", "artist": "ABBA"},
            {"title": "Man! I Feel Like a Woman!", "artist": "Shania Twain"},
            {"title": "Before He Cheats", "artist": "Carrie Underwood"},
            {"title": "Wonderwall", "artist": "Oasis"},
            {"title": "Valerie", "artist": "Amy Winehouse"},
            {"title": "All Star", "artist": "Smash Mouth"},
            {"title": "Since U Been Gone", "artist": "Kelly Clarkson"},
            {"title": "Total Eclipse of the Heart", "artist": "Bonnie Tyler"},
            {"title": "Hey Jude", "artist": "The Beatles"},
            {"title": "Take On Me", "artist": "a-ha"},
            {"title": "Wannabe", "artist": "Spice Girls"},
            {"title": "Ain't No Mountain High Enough", "artist": "Marvin Gaye & Tammi Terrell"},
            {"title": "Piano Man", "artist": "Billy Joel"},
            {"title": "I Will Survive", "artist": "Gloria Gaynor"},
        ]
    },
    {
        "id": "rock_classics",
        "name": "🎸 80s & 90s Rock Anthems",
        "description": "15 legendary guitar riffs and roof-raising rock sing-alongs.",
        "icon": "🎸",
        "track_count": 15,
        "tracks": [
            {"title": "Sweet Child O' Mine", "artist": "Guns N' Roses"},
            {"title": "You Give Love a Bad Name", "artist": "Bon Jovi"},
            {"title": "Pour Some Sugar On Me", "artist": "Def Leppard"},
            {"title": "Smells Like Teen Spirit", "artist": "Nirvana"},
            {"title": "Zombie", "artist": "The Cranberries"},
            {"title": "Hotel California", "artist": "Eagles"},
            {"title": "Creep", "artist": "Radiohead"},
            {"title": "Black Velvet", "artist": "Alannah Myles"},
            {"title": "What's Up?", "artist": "4 Non Blondes"},
            {"title": "Hit Me With Your Best Shot", "artist": "Pat Benatar"},
            {"title": "Highway to Hell", "artist": "AC/DC"},
            {"title": "Dream On", "artist": "Aerosmith"},
            {"title": "Eye of the Tiger", "artist": "Survivor"},
            {"title": "Summer of '69", "artist": "Bryan Adams"},
            {"title": "Barracuda", "artist": "Heart"},
        ]
    },
    {
        "id": "country_favorites",
        "name": "🤠 Country Road Sing-Alongs",
        "description": "15 classic and modern country hits guaranteed to get everyone singing.",
        "icon": "🤠",
        "track_count": 15,
        "tracks": [
            {"title": "Friends in Low Places", "artist": "Garth Brooks"},
            {"title": "Take Me Home, Country Roads", "artist": "John Denver"},
            {"title": "Tennessee Whiskey", "artist": "Chris Stapleton"},
            {"title": "Wagon Wheel", "artist": "Darius Rucker"},
            {"title": "Chicken Fried", "artist": "Zac Brown Band"},
            {"title": "Jolene", "artist": "Dolly Parton"},
            {"title": "Ring of Fire", "artist": "Johnny Cash"},
            {"title": "Need You Now", "artist": "Lady A"},
            {"title": "The Gambler", "artist": "Kenny Rogers"},
            {"title": "Save a Horse (Ride a Cowboy)", "artist": "Big & Rich"},
            {"title": "Cruise", "artist": "Florida Georgia Line"},
            {"title": "Stand By Your Man", "artist": "Tammy Wynette"},
            {"title": "Fast Car", "artist": "Luke Combs"},
            {"title": "Die a Happy Man", "artist": "Thomas Rhett"},
            {"title": "Boot Scootin' Boogie", "artist": "Brooks & Dunn"},
        ]
    },
    {
        "id": "pop_hits",
        "name": "🎤 2000s & Modern Pop Bangers",
        "description": "15 high-energy pop chart-toppers to keep the party moving.",
        "icon": "🎤",
        "track_count": 15,
        "tracks": [
            {"title": "Shake It Off", "artist": "Taylor Swift"},
            {"title": "Rolling in the Deep", "artist": "Adele"},
            {"title": "Uptown Funk", "artist": "Mark Ronson ft. Bruno Mars"},
            {"title": "Party in the U.S.A.", "artist": "Miley Cyrus"},
            {"title": "Roar", "artist": "Katy Perry"},
            {"title": "Bad Romance", "artist": "Lady Gaga"},
            {"title": "Single Ladies (Put a Ring on It)", "artist": "Beyoncé"},
            {"title": "Can't Stop the Feeling!", "artist": "Justin Timberlake"},
            {"title": "Shape of You", "artist": "Ed Sheeran"},
            {"title": "Firework", "artist": "Katy Perry"},
            {"title": "Counting Stars", "artist": "OneRepublic"},
            {"title": "Toxic", "artist": "Britney Spears"},
            {"title": "Happy", "artist": "Pharrell Williams"},
            {"title": "Blinding Lights", "artist": "The Weeknd"},
            {"title": "Levitating", "artist": "Dua Lipa"},
        ]
    }
]

def get_party_pack_by_id(pack_id: str) -> Dict[str, Any]:
    for p in PARTY_PACKS:
        if p["id"] == pack_id:
            return p
    return {}
