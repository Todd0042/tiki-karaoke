"""
Tiki Karaoke - Pre-Party Starter Packs
Curated song collections for offline karaoke parties (camping, tailgates, cabins).
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
            {"title": "Don't Stop Believin'", "artist": "Journey", "video_id": "VcjzHMhBtf0"},
            {"title": "Sweet Caroline", "artist": "Neil Diamond", "video_id": "1vhFnTjia_I"},
            {"title": "Bohemian Rhapsody", "artist": "Queen", "video_id": "fJ9rUzIMcZQ"},
            {"title": "I Want It That Way", "artist": "Backstreet Boys", "video_id": "4fndeDfaWCg"},
            {"title": "Mr. Brightside", "artist": "The Killers", "video_id": "gGdGFtwCNBE"},
            {"title": "Livin' on a Prayer", "artist": "Bon Jovi", "video_id": "lDK9QqIzhwk"},
            {"title": "Dancing Queen", "artist": "ABBA", "video_id": "xFrGuyw1Vm8"},
            {"title": "Man! I Feel Like a Woman!", "artist": "Shania Twain", "video_id": "ZJL4UGSbeFg"},
            {"title": "Before He Cheats", "artist": "Carrie Underwood", "video_id": "WaSy8yy-mr8"},
            {"title": "Wonderwall", "artist": "Oasis", "video_id": "bx1Bh8ZvH84"},
            {"title": "Valerie", "artist": "Amy Winehouse", "video_id": "namF538638A"},
            {"title": "All Star", "artist": "Smash Mouth", "video_id": "L_jWHffIx5E"},
            {"title": "Since U Been Gone", "artist": "Kelly Clarkson", "video_id": "R7UrFYvl5TE"},
            {"title": "Total Eclipse of the Heart", "artist": "Bonnie Tyler", "video_id": "lcOxhH8N3Bo"},
            {"title": "Hey Jude", "artist": "The Beatles", "video_id": "A_MjCqQoLLA"},
            {"title": "Take On Me", "artist": "a-ha", "video_id": "djV11Xbc914"},
            {"title": "Wannabe", "artist": "Spice Girls", "video_id": "gJLIiF15wjQ"},
            {"title": "Ain't No Mountain High Enough", "artist": "Marvin Gaye & Tammi Terrell", "video_id": "Xzpx0Cfx9vs"},
            {"title": "Piano Man", "artist": "Billy Joel", "video_id": "gxEPV4kolz0"},
            {"title": "I Will Survive", "artist": "Gloria Gaynor", "video_id": "ARt9HV9T0w8"},
        ]
    },
    {
        "id": "rock_classics",
        "name": "🎸 80s & 90s Rock Anthems",
        "description": "15 legendary guitar riffs and roof-raising rock sing-alongs.",
        "icon": "🎸",
        "track_count": 15,
        "tracks": [
            {"title": "Sweet Child O' Mine", "artist": "Guns N' Roses", "video_id": "1w7OgIMMRc4"},
            {"title": "You Give Love a Bad Name", "artist": "Bon Jovi", "video_id": "KrZHPOeOxQQ"},
            {"title": "Pour Some Sugar On Me", "artist": "Def Leppard", "video_id": "0UIB9Y4OFPs"},
            {"title": "Smells Like Teen Spirit", "artist": "Nirvana", "video_id": "hTWKbfoikeg"},
            {"title": "Zombie", "artist": "The Cranberries", "video_id": "6Ejga4kJUts"},
            {"title": "Hotel California", "artist": "Eagles", "video_id": "09839DpTctU"},
            {"title": "Creep", "artist": "Radiohead", "video_id": "XFkzRNyygfk"},
            {"title": "Black Velvet", "artist": "Alannah Myles", "video_id": "tT4d1LQy4es"},
            {"title": "What's Up?", "artist": "4 Non Blondes", "video_id": "6NXnxT8rPnw"},
            {"title": "Hit Me With Your Best Shot", "artist": "Pat Benatar", "video_id": "serV18MirGg"},
            {"title": "Highway to Hell", "artist": "AC/DC", "video_id": "l482T0yNkeo"},
            {"title": "Dream On", "artist": "Aerosmith", "video_id": "sZfZ8u_3F4I"},
            {"title": "Eye of the Tiger", "artist": "Survivor", "video_id": "btPJPFnesV4"},
            {"title": "Summer of '69", "artist": "Bryan Adams", "video_id": "eFjjO_lhmn9"},
            {"title": "Barracuda", "artist": "Heart", "video_id": "PeMvK6XxZao"},
        ]
    },
    {
        "id": "country_favorites",
        "name": "🤠 Country Road Sing-Alongs",
        "description": "15 classic and modern country hits guaranteed to get everyone singing.",
        "icon": "🤠",
        "track_count": 15,
        "tracks": [
            {"title": "Friends in Low Places", "artist": "Garth Brooks", "video_id": "mvCgSqPZ4EM"},
            {"title": "Take Me Home, Country Roads", "artist": "John Denver", "video_id": "1vrEljMfXYo"},
            {"title": "Tennessee Whiskey", "artist": "Chris Stapleton", "video_id": "4zAThXFOy2c"},
            {"title": "Wagon Wheel", "artist": "Darius Rucker", "video_id": "hvKyBcCDOB4"},
            {"title": "Chicken Fried", "artist": "Zac Brown Band", "video_id": "e4ujS1er1r0"},
            {"title": "Jolene", "artist": "Dolly Parton", "video_id": "Ixrje2rXLMA"},
            {"title": "Ring of Fire", "artist": "Johnny Cash", "video_id": "mIBTg7q9oGQ"},
            {"title": "Need You Now", "artist": "Lady A", "video_id": "eM213aMKTHg"},
            {"title": "The Gambler", "artist": "Kenny Rogers", "video_id": "7hx4gdlfamo"},
            {"title": "Save a Horse (Ride a Cowboy)", "artist": "Big & Rich", "video_id": "Qt0_gExPW4g"},
            {"title": "Cruise", "artist": "Florida Georgia Line", "video_id": "bmZ9xRO7M9M"},
            {"title": "Stand By Your Man", "artist": "Tammy Wynette", "video_id": "DwBirf4BWew"},
            {"title": "Fast Car", "artist": "Luke Combs", "video_id": "4fWysAoxwE0"},
            {"title": "Die a Happy Man", "artist": "Thomas Rhett", "video_id": "w2CEL4Pwg5c"},
            {"title": "Boot Scootin' Boogie", "artist": "Brooks & Dunn", "video_id": "d05tQrhNMkA"},
        ]
    },
    {
        "id": "pop_hits",
        "name": "🎤 2000s & Modern Pop Bangers",
        "description": "15 high-energy pop chart-toppers to keep the party moving.",
        "icon": "🎤",
        "track_count": 15,
        "tracks": [
            {"title": "Shake It Off", "artist": "Taylor Swift", "video_id": "nfWlot6h_JM"},
            {"title": "Rolling in the Deep", "artist": "Adele", "video_id": "rYEDA3JcQqw"},
            {"title": "Uptown Funk", "artist": "Mark Ronson ft. Bruno Mars", "video_id": "OPf0YbXqDm0"},
            {"title": "Party in the U.S.A.", "artist": "Miley Cyrus", "video_id": "M11SvDtPBhA"},
            {"title": "Roar", "artist": "Katy Perry", "video_id": "CevxZvSJLk8"},
            {"title": "Bad Romance", "artist": "Lady Gaga", "video_id": "qrO4YZeyl0I"},
            {"title": "Single Ladies (Put a Ring on It)", "artist": "Beyoncé", "video_id": "4m1EFMoRFvY"},
            {"title": "Can't Stop the Feeling!", "artist": "Justin Timberlake", "video_id": "ru0K8uYEZWw"},
            {"title": "Shape of You", "artist": "Ed Sheeran", "video_id": "JGwWNGJdvx8"},
            {"title": "Firework", "artist": "Katy Perry", "video_id": "QGJuMBdaqIw"},
            {"title": "Counting Stars", "artist": "OneRepublic", "video_id": "hT_nvWreIhg"},
            {"title": "Toxic", "artist": "Britney Spears", "video_id": "LOZuxwVk7TU"},
            {"title": "Happy", "artist": "Pharrell Williams", "video_id": "ZbZSe6N_BXs"},
            {"title": "Blinding Lights", "artist": "The Weeknd", "video_id": "4NRXx6U8ABQ"},
            {"title": "Levitating", "artist": "Dua Lipa", "video_id": "TUVcZfQe-Kw"},
        ]
    }
]

def get_party_pack_by_id(pack_id: str) -> Dict[str, Any]:
    for p in PARTY_PACKS:
        if p["id"] == pack_id:
            return p
    return {}
