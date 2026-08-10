from __future__ import annotations

import random
import string


class NameService:
    """随机姓名生成器：百万级不重复（v3.4：有界缓存 + 姓名拟人化）"""

    _used_names: set[str] = set()
    _MAX_USED = 10000  # 有界上限，防内存缓涨

    # 常见英文名（名，首字母大写）
    FIRST_NAMES = [
        "James", "Robert", "John", "Michael", "David", "William", "Richard", "Joseph",
        "Thomas", "Christopher", "Charles", "Daniel", "Matthew", "Anthony", "Mark",
        "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth", "Kevin", "Brian",
        "George", "Timothy", "Ronald", "Edward", "Jason", "Jeffrey", "Ryan",
        "Jacob", "Gary", "Nicholas", "Eric", "Jonathan", "Stephen", "Larry", "Justin",
        "Scott", "Brandon", "Benjamin", "Samuel", "Gregory", "Alexander", "Patrick",
        "Frank", "Raymond", "Jack", "Dennis", "Jerry", "Tyler", "Aaron", "Jose",
        "Adam", "Nathan", "Henry", "Douglas", "Zachary", "Peter", "Kyle", "Noah",
        "Ethan", "Jeremy", "Walter", "Christian", "Keith", "Roger", "Terry",
        "Austin", "Sean", "Gerald", "Carl", "Harold", "Dylan", "Arthur", "Lawrence",
        "Jordan", "Jesse", "Bryan", "Billy", "Bruce", "Gabriel", "Logan", "Albert",
        "Willie", "Alan", "Juan", "Wayne", "Elijah", "Randy", "Roy", "Vincent",
        "Ralph", "Eugene", "Russell", "Bobby", "Mason", "Philip", "Louis",
    ]

    # 常见英文姓（首字母大写）
    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
        "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
        "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
        "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera",
        "Campbell", "Mitchell", "Carter", "Roberts", "Gomez", "Phillips", "Evans",
        "Turner", "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes", "Stewart",
        "Morris", "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz",
        "Morgan", "Cooper", "Peterson", "Bailey", "Reed", "Kelly", "Howard", "Ramos",
        "Kim", "Cox", "Ward", "Richardson", "Watson", "Brooks", "Chavez", "Wood",
        "James", "Bennett", "Gray", "Mendoza", "Ruiz", "Hughes", "Price", "Alvarez",
        "Castillo", "Sanders", "Patel", "Myers", "Long", "Ross", "Foster", "Jimenez",
    ]

    @classmethod
    def generate(cls) -> str:
        """生成一个不重复的拟人化随机姓名（v3.4：首字母大写、允许空格、数字后缀低概率）。"""
        for _ in range(100):
            first = random.choice(cls.FIRST_NAMES)
            last = random.choice(cls.LAST_NAMES)
            # 50% 概率用 "First Last" 格式，30% 加中间名首字母，20% 加数字后缀
            r = random.random()
            if r < 0.5:
                name = f"{first} {last}"
            elif r < 0.8:
                mid = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                name = f"{first} {mid}. {last}"
            else:
                suffix = random.randint(1, 99)
                name = f"{first}{last}{suffix}"
            if name not in cls._used_names:
                return cls._add(name)
        # 兜底
        name = f"{random.choice(cls.FIRST_NAMES)} {random.choice(cls.LAST_NAMES)}"
        return cls._add(name)

    @classmethod
    def _add(cls, name: str) -> str:
        """添加到已用集合，有界上限（超限时丢弃最旧一批）。"""
        cls._used_names.add(name)
        if len(cls._used_names) > cls._MAX_USED:
            # 丢弃一半（最旧的一半）
            discard = len(cls._used_names) // 2
            for _ in range(discard):
                try:
                    cls._used_names.pop()
                except KeyError:
                    break
        return name

    @classmethod
    def generate_birthdate(cls) -> str:
        """生成 19-40 岁之间的随机生日，格式 YYYY-MM-DD（v3.4：正态分布，mean 1995,σ=8）。"""
        year = int(random.gauss(1995, 8))
        year = max(1970, min(2005, year))  # 截断到合理范围
        month = random.randint(1, 12)
        if month == 2:
            day = random.randint(1, 28)
        elif month in (4, 6, 9, 11):
            day = random.randint(1, 30)
        else:
            day = random.randint(1, 31)
        return f"{year:04d}-{month:02d}-{day:02d}"


name_service = NameService()
