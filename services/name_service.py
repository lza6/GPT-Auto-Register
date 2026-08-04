from __future__ import annotations

import random
import string


class NameService:
    """随机姓名生成器：百万级不重复"""

    _used_names: set[str] = set()

    # 常见英文名（名）
    FIRST_NAMES = [
        "james", "robert", "john", "michael", "david", "william", "richard", "joseph",
        "thomas", "christopher", "charles", "daniel", "matthew", "anthony", "mark",
        "donald", "steven", "paul", "andrew", "joshua", "kenneth", "kevin", "brian",
        "george", "timothy", "ronald", "edward", "jason", "jeffrey", "ryan",
        "jacob", "gary", "nicholas", "eric", "jonathan", "stephen", "larry", "justin",
        "scott", "brandon", "benjamin", "samuel", "gregory", "alexander", "patrick",
        "frank", "raymond", "jack", "dennis", "jerry", "tyler", "aaron", "jose",
        "adam", "nathan", "henry", "douglas", "zachary", "peter", "kyle", "noah",
        "ethan", "jeremy", "walter", "christian", "keith", "roger", "terry",
        "austin", "sean", "gerald", "carl", "harold", "dylan", "arthur", "lawrence",
        "jordan", "jesse", "bryan", "billy", "bruce", "gabriel", "logan", "albert",
        "willie", "alan", "juan", "wayne", "elijah", "randy", "roy", "vincent",
        "ralph", "eugene", "russell", "bobby", "mason", "philip", "louis",
    ]

    # 常见英文姓
    LAST_NAMES = [
        "smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis",
        "rodriguez", "martinez", "hernandez", "lopez", "gonzalez", "wilson", "anderson",
        "thomas", "taylor", "moore", "jackson", "martin", "lee", "perez", "thompson",
        "white", "harris", "sanchez", "clark", "ramirez", "lewis", "robinson",
        "walker", "young", "allen", "king", "wright", "scott", "torres", "nguyen",
        "hill", "flores", "green", "adams", "nelson", "baker", "hall", "rivera",
        "campbell", "mitchell", "carter", "roberts", "gomez", "phillips", "evans",
        "turner", "diaz", "parker", "cruz", "edwards", "collins", "reyes", "stewart",
        "morris", "morales", "murphy", "cook", "rogers", "gutierrez", "ortiz",
        "morgan", "cooper", "peterson", "bailey", "reed", "kelly", "howard", "ramos",
        "kim", "cox", "ward", "richardson", "watson", "brooks", "chavez", "wood",
        "james", "bennett", "gray", "mendoza", "ruiz", "hughes", "price", "alvarez",
        "castillo", "sanders", "patel", "myers", "long", "ross", "foster", "jimenez",
    ]

    @classmethod
    def generate(cls) -> str:
        """生成一个不重复的随机姓名"""
        for _ in range(100):
            first = random.choice(cls.FIRST_NAMES)
            last = random.choice(cls.LAST_NAMES)
            # 随机加数字后缀增加唯一性
            suffix = random.randint(1, 9999)
            name = f"{first}{last}{suffix}"
            if name not in cls._used_names:
                cls._used_names.add(name)
                return name
        # 兜底：纯随机
        rand = ''.join(random.choices(string.ascii_lowercase, k=8))
        name = f"{rand}{random.randint(1000, 9999)}"
        cls._used_names.add(name)
        return name

    @classmethod
    def generate_birthdate(cls) -> str:
        """生成 19-40 岁之间的随机生日，格式 YYYY-MM-DD"""
        year = random.randint(1986, 2007)
        month = random.randint(1, 12)
        # 确保日期合法
        if month == 2:
            day = random.randint(1, 28)
        elif month in (4, 6, 9, 11):
            day = random.randint(1, 30)
        else:
            day = random.randint(1, 31)
        return f"{year:04d}-{month:02d}-{day:02d}"


name_service = NameService()
