import logging
import random
import json
import os
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage

API_TOKEN = ''
BOT_USERNAME = 'WITHOUT @'  

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

DATA_FILE = 'data.json'

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
else:
    data = {}

admin_ids = {1097277508}
subscribers = set(data.get('subscribers', []))      
user_profiles = data.get('user_profiles', {})        
chat_list = data.get('chat_list', {})             
active_games = {}

SHOP_ITEMS = {
    'Буст': 50,
    'КакойтоТовар': 100
}
bonus_settings = {"villager": 10, "mafia": 10}
SUBSCRIPTION_PRICE = 100

def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'user_profiles': user_profiles,
            'chat_list': chat_list,
            'subscribers': list(subscribers)
        }, f, ensure_ascii=False, indent=4)


def get_user_profile(user_id, username):
    if str(user_id) not in user_profiles:
        user_profiles[str(user_id)] = {
            "username": username,
            "balance": 0,
            "points": 0,
            "roses": 0,
            "donation": 0
        }
    else:
        user_profiles[str(user_id)]["username"] = username
    save_data()
    return user_profiles[str(user_id)]

def update_chat_list(chat: types.Chat):
    if str(chat.id) not in chat_list:
        chat_list[str(chat.id)] = {"title": chat.title or "Без названия", "link": None, "status": "не проверен", "score": 0}
        save_data()


class Role:
    def __init__(self, name, description, abilities):
        self.name = name
        self.description = description
        self.abilities = abilities

STANDARD_ROLES = {
    'Мирный житель': Role('Мирный житель', 'Нет специальных способностей', {}),
    'Мафия': Role('Мафия', 'Убийство ночью', {'kill': True}),
    'Дон мафии': Role('Дон мафии', 'Управляет мафией, убийство ночью', {'kill': True, 'lead': True}),
    'Доктор': Role('Доктор', 'Спасение игрока ночью', {'save': True}),
    'Комиссар': Role('Комиссар', 'Проверка роли игрока ночью', {'check': True})
}

ABILITY_DESCRIPTIONS = {
    'kill': 'Убить игрока ночью',
    'save': 'Спасти игрока ночью',
    'check': 'Проверить роль игрока ночью',
    'block': 'Блокировать действие игрока',
    'boost': 'Усилить способности игрока',
    'spy': 'Получить информацию о действии игрока',
    'lead': 'Управлять мафией'
}

user_game_map = {}

class Game:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = {}
        self.phase = 'waiting'  
        self.votes = {}         
        self.night_actions = {}
        self.started = False

    def add_player(self, user_id, username):
        if user_id not in self.players:
            self.players[user_id] = {'username': username, 'role': None, 'alive': True, 'currency': 0}
            return True
        return False

    def assign_roles(self):
        player_ids = list(self.players.keys())
        random.shuffle(player_ids)
        n = len(player_ids)
        roles_list = []
        if n >= 5:
            roles_list = ['Мафия', 'Дон мафии', 'Доктор', 'Комиссар'] + ['Мирный житель'] * (n - 4)
        else:
            roles_list = ['Мирный житель'] * n
        random.shuffle(roles_list)
        for uid, role_name in zip(player_ids, roles_list):
            self.players[uid]['role'] = STANDARD_ROLES[role_name]
        self.started = True

    def tally_votes(self):
        vote_count = {}
        for voter, target in self.votes.items():
            vote_count[target] = vote_count.get(target, 0) + 1
        if vote_count:
            max_votes = max(vote_count.values())
            targets = [uid for uid, count in vote_count.items() if count == max_votes]
            if len(targets) == 1:
                return targets[0]
        return None

    def process_night(self):
        effective_actions = dict(self.night_actions)
        blocked_targets = {action['target'] for action in self.night_actions.values() if action['action'] == 'block'}
        for actor in list(effective_actions.keys()):
            if actor in blocked_targets:
                del effective_actions[actor]
        mafia_kills = [action['target'] for actor, action in effective_actions.items() if action['action'] == 'kill']
        target = max(set(mafia_kills), key=mafia_kills.count) if mafia_kills else None
        saved = None
        for action in effective_actions.values():
            if action['action'] == 'save':
                saved = action['target']
                break
        check_result = {}
        for action in effective_actions.values():
            if action['action'] == 'check':
                target_id = action['target']
                role = self.players[target_id]['role']
                check_result[target_id] = role.name
        boosted = [action['target'] for action in effective_actions.values() if action['action'] == 'boost']
        spy_info = {}
        for actor, action in effective_actions.items():
            if action['action'] == 'spy':
                target_id = action['target']
                spy_info[actor] = self.night_actions.get(target_id, {}).get('action', 'ничего')
        result = {
            'killed': None,
            'checked': check_result,
            'boosted': boosted,
            'spy_info': spy_info
        }
        if target is not None and target != saved:
            self.players[target]['alive'] = False
            result['killed'] = target
        self.night_actions = {}
        return result

    def check_winner(self):
        mafia_count = 0
        villagers_count = 0
        for player in self.players.values():
            if player['alive']:
                if player['role'].name in ['Мафия', 'Дон мафии']:
                    mafia_count += 1
                else:
                    villagers_count += 1
        if mafia_count == 0:
            return 'Мирные жители'
        elif mafia_count >= villagers_count:
            return 'Мафия'
        return None


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    args = message.get_args()

    if message.chat.type == "private":
        if args and args.startswith("join_"):
            try:
                chat_id = int(args.split("_", 1)[1])
            except:
                await message.answer("Неверный параметр для присоединения.")
                return

            if chat_id not in active_games:
                await message.answer("Игра в указанном чате не найдена.")
                return

            game = active_games[chat_id]
            user_id = message.from_user.id
            username = message.from_user.username or message.from_user.first_name

            if game.add_player(user_id, username):
                user_game_map[user_id] = chat_id 
                await message.answer(f"Вы успешно присоединились к игре в чате «{chat_id}»!")
            else:
                await message.answer("Вы уже участвуете в этой игре.")
            return
        else:
            await message.answer(                "👋 Привет! Добро пожаловать в *Мафия Bot* 🎲\n\n"
                "🔎 Узнай список команд бота, используя команду *`/help`*.\n"
                "🤝 Присоединиться к игре можно через ссылку-приглашение полученую из чата, или создать в чате самому.\n\n"
                "Желаем удачи и весёлой игры! 🎉")
            return

    if message.chat.type in ["group", "supergroup"]:
        update_chat_list(message.chat)
        await message.answer("Добро пожаловать в игру «Мафия»! Используйте /newgame для создания новой игры.")

@dp.message_handler(commands=['add_donation'])
async def cmd_add_donation(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("🚫 Нет доступа.")
        return
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Используйте: /add_donation <user_id или username> <сумма>")
        return
    target_identifier = args[0]
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("❗ Сумма должна быть числом.")
        return

    target_profile = None
    for uid, profile in user_profiles.items():
        if str(uid) == target_identifier or profile["username"] == target_identifier:
            target_profile = profile
            break

    if not target_profile:
        try:
            new_user_id = int(target_identifier)
            target_profile = get_user_profile(new_user_id, f"user{new_user_id}")
            await message.answer("Профиль пользователя не был найден. Создан новый профиль по умолчанию.")
        except ValueError:
            await message.answer("❗ Целевой пользователь не найден.")
            return

    target_profile["donation"] += amount
    save_data() 
    await message.answer(
        f"💳 Донатный баланс пользователя {target_profile['username']} пополнен на {amount}.\n"
        f"Новый донатный баланс: {target_profile['donation']}"
    )


@dp.message_handler(commands=['newgame'])
async def cmd_newgame(message: types.Message):
    if message.chat.type == "private":
        await message.answer("Создавать игры можно только в групповых чатах.")
        return
    chat_id = message.chat.id
    if chat_id in active_games and not active_games[chat_id].started:
        await message.answer("Игра уже создана. Присоединяйтесь командой /join.")
    else:
        game = Game(chat_id)
        active_games[chat_id] = game
        join_link = f"https://t.me/{BOT_USERNAME}?start=join_{str(chat_id)}"
        keyboard = InlineKeyboardMarkup()
        button = InlineKeyboardButton(text="Ссылка присоединиться", url=join_link)
        keyboard.add(button)
        await message.answer("Новая игра создана! Для участия используйте кнопку ниже:", reply_markup=keyboard)


@dp.message_handler(commands=['join'])
async def cmd_join(message: types.Message):
    if message.chat.type == "private":
        await message.answer("В личном чате игры создавать нельзя. Используйте ссылку-приглашение для присоединения.")
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    if chat_id not in active_games:
        await message.answer("Нет активной игры. Создайте новую с помощью /newgame.")
        return
    game = active_games[chat_id]
    if game.started:
        await message.answer("Игра уже началась, присоединиться нельзя.")
        return
    if game.add_player(user_id, username):
        await message.answer(f"{username} присоединился к игре!")
    else:
        await message.answer("Вы уже в игре.")

@dp.message_handler(commands=['startgame'])
async def cmd_startgame(message: types.Message):
    if message.chat.type == "private":
        await message.answer("Начать игру можно только в групповых чатах.")
        return
    chat_id = message.chat.id
    if chat_id not in active_games:
        await message.answer("Нет активной игры для начала.")
        return
    game = active_games[chat_id]
    if len(game.players) < 5:
        await message.answer("Недостаточно игроков для начала игры (минимум 5).")
        return
    game.assign_roles()
    for user_id, data in game.players.items():
        role = data['role']
        message_text = f"Ваша роль: {role.name}\nОписание: {role.description}\n"
        if role.abilities:
            message_text += "Возможности:\n"
            for ability in role.abilities:
                description = ABILITY_DESCRIPTIONS.get(ability, "Нет описания")
                message_text += f"- {ability}: {description} (команда: /action {ability} <username или user_id>)\n"
        else:
            message_text += "Нет специальных возможностей."
        try:
            await bot.send_message(user_id, message_text)
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения игроку {user_id}: {e}")
    game.phase = 'day'
    await message.answer("Игра началась! Дневной раунд – обсуждение и голосование с помощью /vote.")

@dp.message_handler(commands=['vote'])
async def cmd_vote(message: types.Message):
    if message.chat.type == "private":
        user_id = message.from_user.id
        if user_id not in user_game_map:
            await message.answer("Вы не присоединились ни к одной игре. Используйте ссылку-приглашение.")
            return

        chat_id = user_game_map[user_id]
    else:
        chat_id = message.chat.id

    if chat_id not in active_games:
        await message.answer("Нет активной игры.")
        return

    game = active_games[chat_id]
    if game.phase != 'day':
        await message.answer("Голосование доступно только днем.")
        return

    args = message.get_args().split()
    if not args:
        await message.answer("Используйте формат: /vote <username или user_id>")
        return

    target_identifier = args[0]
    target_id = None
    for uid, data in game.players.items():
        if str(uid) == target_identifier or data['username'] == target_identifier:
            target_id = uid
            break

    if target_id is None:
        await message.answer("Игрок не найден.")
        return

    game.votes[user_id] = target_id
    await message.answer(f"Ваш голос за игрока {game.players[target_id]['username']} засчитан.")


@dp.message_handler(commands=['endvote'])
async def cmd_endvote(message: types.Message):
    if message.chat.type == "private":
        await message.answer("Команда /endvote доступна только в групповых чатах.")
        return
    chat_id = message.chat.id
    if chat_id not in active_games:
        await message.answer("Нет активной игры.")
        return
    game = active_games[chat_id]
    if game.phase != 'day':
        await message.answer("Голосование доступно только днем.")
        return
    eliminated = game.tally_votes()
    if eliminated:
        game.players[eliminated]['alive'] = False
        await message.answer(f"Игрок {game.players[eliminated]['username']} был изгнан.")
    else:
        await message.answer("Не удалось определить игрока для изгнания.")
    game.votes = {}
    winner = game.check_winner()
    if winner:
        await message.answer(f"Победа за: {winner}!")
        for uid, data in game.players.items():
            if data['alive']:
                if winner == 'Мирные жители' and data['role'].name not in ['Мафия', 'Дон мафии']:
                    data['currency'] += bonus_settings["villager"]
                elif winner == 'Мафия' and data['role'].name in ['Мафия', 'Дон мафии']:
                    data['currency'] += bonus_settings["mafia"]
        del active_games[chat_id]
    else:
        game.phase = 'night'
        await message.answer("Ночной раунд начался. Для ночных действий используйте /action.")

@dp.message_handler(commands=['action'])
async def cmd_action(message: types.Message):
    if message.chat.type == "private":
        user_id = message.from_user.id
        if user_id not in user_game_map:
            await message.answer("Вы не присоединились ни к одной игре. Используйте ссылку-приглашение.")
            return

        chat_id = user_game_map[user_id]  
    else:
        chat_id = message.chat.id

    if chat_id not in active_games:
        await message.answer("Нет активной игры.")
        return

    game = active_games[chat_id]
    if game.phase != 'night':
        await message.answer("Ночные действия доступны только ночью.")
        return

    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Используйте формат: /action <kill/save/check/block/boost/spy> <username или user_id>")
        return

    action_type = args[0]
    target_identifier = args[1]
    target_id = None

    for uid, data in game.players.items():
        if str(uid) == target_identifier or data['username'] == target_identifier:
            target_id = uid
            break

    if target_id is None:
        await message.answer("Игрок не найден.")
        return

    role = game.players[user_id]['role']

    if action_type == 'kill' and role.abilities.get('kill'):
        game.night_actions[user_id] = {'action': 'kill', 'target': target_id}
        await message.answer("Действие убийства зарегистрировано.")
    elif action_type == 'save' and role.abilities.get('save'):
        game.night_actions[user_id] = {'action': 'save', 'target': target_id}
        await message.answer("Действие спасения зарегистрировано.")
    elif action_type == 'check' and role.abilities.get('check'):
        game.night_actions[user_id] = {'action': 'check', 'target': target_id}
        await message.answer("Действие проверки зарегистрировано.")
    elif action_type == 'block' and role.abilities.get('block'):
        game.night_actions[user_id] = {'action': 'block', 'target': target_id}
        await message.answer("Действие блокировки зарегистрировано.")
    elif action_type == 'boost' and role.abilities.get('boost'):
        game.night_actions[user_id] = {'action': 'boost', 'target': target_id}
        await message.answer("Действие усиления зарегистрировано.")
    elif action_type == 'spy' and role.abilities.get('spy'):
        game.night_actions[user_id] = {'action': 'spy', 'target': target_id}
        await message.answer("Действие шпионажа зарегистрировано.")
    else:
        await message.answer("Ваша роль не обладает такой способностью или действие указано неверно.")


@dp.message_handler(commands=['endnight'])
async def cmd_endnight(message: types.Message):
    if message.chat.type == "private":
        await message.answer("Команда /endnight доступна только в групповых чатах.")
        return

    chat_id = message.chat.id
    if chat_id not in active_games:
        await message.answer("Нет активной игры.")
        return

    game = active_games[chat_id]
    if game.phase != 'night':
        await message.answer("Ночной раунд не активен.")
        return

    results = game.process_night()
    text = "Ночная фаза завершена!\n"

    if results['killed']:
        victim_id = results['killed']
        victim = game.players[victim_id]['username']
        text += f"🔪 Игрок {victim} был убит ночью.\n"

        try:
            await bot.send_message(victim_id, "😵 Вы были убиты ночью! Вы больше не участвуете в игре.")
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения игроку {victim_id}: {e}")

    else:
        text += "🌙 Никто не был убит ночью.\n"

    if results['checked']:
        for uid, role_name in results['checked'].items():
            try:
                await bot.send_message(uid, f"🔍 Комиссар проверил вас: ваша роль — {role_name}.")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения {uid}: {e}")

    if results['boosted']:
        for uid in results['boosted']:
            try:
                await bot.send_message(uid, "💪 Вы были усилены ночью!")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения {uid}: {e}")

    if results['spy_info']:
        for spy_id, act in results['spy_info'].items():
            try:
                await bot.send_message(spy_id, f"🕵️‍♂️ Вы шпионили за игроком. Он совершил действие: {act}.")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения {spy_id}: {e}")

    game.night_actions = {}

    winner = game.check_winner()
    if winner:
        text += f"\n🏆 Победила команда: {winner}!\n"
        for uid, data in game.players.items():
            if data['alive']:
                if winner == 'Мирные жители' and data['role'].name not in ['Мафия', 'Дон мафии']:
                    data['currency'] += bonus_settings["villager"]
                elif winner == 'Мафия' and data['role'].name in ['Мафия', 'Дон мафии']:
                    data['currency'] += bonus_settings["mafia"]

        del active_games[chat_id]  
    else:
        game.phase = 'day'  
        text += "☀️ Дневной раунд начался. Голосуйте с помощью /vote."

    await message.answer(text)


@dp.message_handler(commands=['create_role'])
async def cmd_create_role(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id not in subscribers:
        await message.answer("Эта функция доступна только для подписчиков или администраторов.")
        return
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Формат: /create_role <название> <способности (kill/save/check/block/boost/spy)>")
        return
    role_name = args[0]
    abilities = {}
    for ability in args[1:]:
        if ability in ['kill', 'save', 'check', 'block', 'boost', 'spy']:
            abilities[ability] = True
    STANDARD_ROLES[role_name] = Role(role_name, 'Кастомная роль', abilities)
    await message.answer(f"Кастомная роль {role_name} создана с возможностями: {', '.join(abilities.keys())}")

@dp.message_handler(commands=['edit_role'])
async def cmd_edit_role(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids and user_id not in subscribers:
        await message.answer("Эта функция доступна только для подписчиков или администраторов.")
        return
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Формат: /edit_role <название> <способности (kill/save/check/block/boost/spy)>")
        return
    role_name = args[0]
    if role_name not in STANDARD_ROLES:
        await message.answer("Роль не найдена.")
        return
    if role_name in ['Мирный житель', 'Мафия', 'Дон мафии', 'Доктор', 'Комиссар']:
        await message.answer("Редактирование стандартных ролей не разрешено.")
        return
    abilities = {}
    for ability in args[1:]:
        if ability in ['kill', 'save', 'check', 'block', 'boost', 'spy']:
            abilities[ability] = True
    STANDARD_ROLES[role_name].abilities = abilities
    await message.answer(f"Кастомная роль {role_name} обновлена с возможностями: {', '.join(abilities.keys()) if abilities else 'нет специальных способностей'}")

@dp.message_handler(commands=['subscribe'])
async def cmd_subscribe(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    if user_id in subscribers:
        await message.answer("ℹ️ Вы уже подписаны на премиум-функции.")
        return
    if profile["donation"] < SUBSCRIPTION_PRICE:
        await message.answer(
            f"💰 Для подписки требуется донатный баланс не менее {SUBSCRIPTION_PRICE}. "
            f"Ваш донатный баланс: {profile['donation']}."
        )
        return
    profile["donation"] -= SUBSCRIPTION_PRICE
    subscribers.add(user_id)
    save_data()
    await message.answer("✅ Вы успешно подписались на премиум-функции!")


@dp.message_handler(commands=['shop'])
async def cmd_shop(message: types.Message):
    text = "Доступные товары:\n"
    for item, price in SHOP_ITEMS.items():
        text += f"{item} – {price} валюты\n"
    await message.answer(text)

@dp.message_handler(commands=['buy'])
async def cmd_buy(message: types.Message):
    args = message.get_args().split(maxsplit=1)
    if not args:
        await message.answer("Используйте: /buy <название товара>")
        return
    item_name = args[0]
    if item_name not in SHOP_ITEMS:
        await message.answer("Товар не найден.")
        return
    price = SHOP_ITEMS[item_name]
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    if profile["balance"] < price:
        await message.answer("Недостаточно средств для покупки.")
        return
    profile["balance"] -= price
    save_data()
    await message.answer(f"Вы успешно купили {item_name} за {price} валюты!")

@dp.message_handler(commands=['stop'])
async def cmd_stop(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    if chat_id in active_games:
        del active_games[chat_id]
        await message.answer("Игра остановлена.")
    else:
        await message.answer("Нет активной игры для остановки.")

@dp.message_handler(commands=['end'])
async def cmd_end(message: types.Message):
    await cmd_stop(message)

@dp.message_handler(commands=['leave'])
async def cmd_leave(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if chat_id in active_games:
        game = active_games[chat_id]
        if user_id in game.players:
            del game.players[user_id]
            await message.answer("Вы покинули игру.")
        else:
            await message.answer("Вы не участвуете в игре.")
    else:
        await message.answer("Нет активной игры.")

@dp.message_handler(commands=['set_bonus'])
async def cmd_set_bonus(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    args = message.get_args().split()
    if len(args) != 2:
        await message.answer("Используйте: /set_bonus <villager/mafia> <сумма>")
        return
    group = args[0].lower()
    if group not in ['villager', 'mafia']:
        await message.answer("Группа должна быть 'villager' или 'mafia'.")
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    bonus_settings[group] = amount
    await message.answer(f"Бонус для {group} обновлен до {amount} валюты.")

@dp.message_handler(commands=['subscribers'])
async def cmd_subscribers(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    if not subscribers:
        await message.answer("Нет подписчиков.")
        return
    subs = ", ".join(str(uid) for uid in subscribers)
    await message.answer("Список подписчиков: " + subs)

@dp.message_handler(commands=['profile'])
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    profile = get_user_profile(user_id, username)
    text = (f"Профиль {profile['username']}:\n"
            f"Баланс: {profile['balance']}\n"
            f"Очки: {profile['points']}\n"
            f"Розы: {profile['roses']}")
    await message.answer(text)

@dp.message_handler(commands=['game'])
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        user_id = message.from_user.id
        if user_id not in user_game_map:
            await message.answer("Вы не присоединились ни к одной игре. Используйте ссылку-приглашение.")
            return

        chat_id = user_game_map[user_id]  
    else:
        chat_id = message.chat.id

    if chat_id not in active_games:
        await message.answer("Нет активной игры.")
        return

    game = active_games[chat_id]
    players = ", ".join([data['username'] for data in game.players.values()])
    await message.answer(f"Активная игра в чате {chat_id}:\nФаза: {game.phase}\nИгроки: {players}")


@dp.message_handler(commands=['time'])
async def cmd_time(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_games:
        game = active_games[chat_id]
        await message.answer(f"Текущее время игры: Фаза – {game.phase}")
    else:
        await message.answer("Нет активной игры.")

@dp.message_handler(commands=['extend'])
async def cmd_extend(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_games:
        await message.answer("Время раунда продлено.")
    else:
        await message.answer("Нет активной игры.")

@dp.message_handler(commands=['next'])
async def cmd_next(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_games:
        game = active_games[chat_id]
        if game.phase == 'day':
            game.phase = 'night'
            await message.answer("Переход к ночному раунду.")
        elif game.phase == 'night':
            game.phase = 'day'
            await message.answer("Переход к дневному раунду.")
        else:
            await message.answer("Неизвестная фаза.")
    else:
        await message.answer("Нет активной игры.")

@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    help_keyboard = InlineKeyboardMarkup(row_width=2)
    help_keyboard.add(
        InlineKeyboardButton("Команды игры", callback_data="help_game"),
        InlineKeyboardButton("Команды бота", callback_data="help_bot")
    )
    await message.answer("Выберите категорию команд:", reply_markup=help_keyboard)

@dp.callback_query_handler(lambda c: c.data in ["help_game", "help_bot"])
async def process_help_callback(callback_query: types.CallbackQuery):
    data = callback_query.data
    if data == "help_game":
        text = (
            "<b>Команды игры:</b>\n"
            "/newgame - Создать новую игру в групповом чате.\n"
            "/join - Присоединиться к активной игре в групповом чате.\n"
            "/startgame - Запустить игру (минимум 5 игроков).\n"
            "/vote - Отдать голос за игрока во время дневного раунда.\n"
            "/endvote - Завершить голосование и изгнать игрока.\n"
            "/action - Выполнить ночное действие (например, убийство или спасение).\n"
            "/endnight - Завершить ночной раунд и обработать действия игроков.\n"
            "/game - Получить информацию об активной игре.\n"
            "/time - Узнать текущую фазу игры.\n"
            "/extend - Продлить время раунда.\n"
            "/next - Перейти к следующему раунду.\n"
            "/case - Открыть кейс и получить случайное вознаграждение."
        )
    else:
        text = (
            "<b>Команды бота:</b>\n"
            "/start - Запуск бота и присоединение к игре через ссылку-приглашение.\n"
            "/profile - Просмотреть профиль (баланс, очки, розы).\n"
            "/add_chat_to_list - добавить чат в список\n"
            "/balance (/amount) - Проверить баланс пользователя.\n"
            "/give - Перевести валюту другому игроку.\n"
            "/shop - Просмотреть товары магазина.\n"
            "/buy - Купить товар из магазина.\n"
            "/subscribe - Подписаться на премиум-функции.\n"
            "/top - Посмотреть топ пользователей по очкам.\n"
            "/points - Узнать свои очки.\n"
            "/top_chats - Просмотреть топ чатов по активности.\n"
            "/top_global - Узнать глобальный рейтинг пользователей.\n"
            "/points_global - Проверить глобальные очки.\n"
            "/rose - Отправить розы другому пользователю.\n"
            "/voice_rose - Отправить голосовые розы.\n"
            "/chats - Просмотреть список чатов.\n"
            "/roses_amount - Проверить количество роз.\n"
            "/feedback - Отправить отзыв разработчику."
        )
    back_keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="help_back"))
    await callback_query.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "help_back")
async def process_help_back(callback_query: types.CallbackQuery):
    help_keyboard = InlineKeyboardMarkup(row_width=2)
    help_keyboard.add(
        InlineKeyboardButton("Команды игры", callback_data="help_game"),
        InlineKeyboardButton("Команды бота", callback_data="help_bot")
    )
    await callback_query.message.edit_text("Выберите категорию команд:", reply_markup=help_keyboard)
    await callback_query.answer()

@dp.message_handler(commands=['settings'])
async def cmd_settings(message: types.Message):
    await message.answer("Настройки пользователя пока не доступны.")

@dp.message_handler(commands=['balance'])
async def cmd_balance(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"Ваш баланс: {profile['balance']}")

@dp.message_handler(commands=['give'])
async def cmd_give(message: types.Message):
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Используйте: /give <user_id или username> <сумма>")
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("Неверная сумма.")
        return
    sender_id = message.from_user.id
    sender_profile = get_user_profile(sender_id, message.from_user.username or message.from_user.first_name)
    if sender_profile["balance"] < amount:
        await message.answer("Недостаточно средств.")
        return
    target_identifier = args[0]
    target_profile = None
    for uid, profile in user_profiles.items():
        if str(uid) == target_identifier or profile["username"] == target_identifier:
            target_profile = profile
            break
    if not target_profile:
        await message.answer("Целевой пользователь не найден.")
        return
    sender_profile["balance"] -= amount
    target_profile["balance"] += amount
    save_data()
    await message.answer(f"Вы перевели {amount} валюты пользователю {target_profile['username']}.")

@dp.message_handler(commands=['prolong'])
async def cmd_prolong(message: types.Message):
    await cmd_extend(message)

@dp.message_handler(commands=['reload_admins'])
async def cmd_reload_admins(message: types.Message):
    global admin_ids
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    admin_ids = {1097277508}
    await message.answer("Список администраторов обновлен.")

@dp.message_handler(commands=['feedback'])
async def cmd_feedback(message: types.Message):
    feedback_text = message.get_args()
    if not feedback_text:
        await message.answer("Пожалуйста, напишите отзыв после команды.")
        return
    admin_id = next(iter(admin_ids))
    await bot.send_message(admin_id, f"Отзыв от {message.from_user.username or message.from_user.first_name}:\n{feedback_text}")
    await message.answer("Спасибо за ваш отзыв!")

@dp.message_handler(commands=["id"])
async def send_chat_id(message: types.Message):
    chat_id = message.chat.id
    await message.reply(f"ID этого чата: `{chat_id}`", parse_mode="Markdown")

@dp.message_handler(commands=['top'])
async def cmd_top(message: types.Message):
    if not user_profiles:
        await message.answer("Нет данных для отображения рейтинга.")
        return
    sorted_users = sorted(user_profiles.items(), key=lambda item: item[1]["points"], reverse=True)
    text = "Топ пользователей по очкам:\n"
    for i, (uid, profile) in enumerate(sorted_users[:10], 1):
        text += f"{i}. {profile['username']} - {profile['points']} очков\n"
    await message.answer(text)

@dp.message_handler(commands=['points'])
async def cmd_points(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"Ваши очки: {profile['points']}")

@dp.message_handler(commands=['exit'])
async def cmd_exit(message: types.Message):
    await cmd_leave(message)

@dp.message_handler(commands=['top_chats'])
async def cmd_top_chats(message: types.Message):
    if not chat_list:
        await message.answer("Нет данных по чатам.")
        return
    sorted_chats = sorted(chat_list.items(), key=lambda item: item[1].get("score", 0), reverse=True)
    text = "Топ чатов:\n"
    for i, (chat_id, chat_info) in enumerate(sorted_chats[:10], 1):
        text += f"{i}. {chat_info.get('title', 'Без названия')} - {chat_info.get('score', 0)}\n"
    await message.answer(text)

@dp.message_handler(commands=['top_global'])
async def cmd_top_global(message: types.Message):
    if not user_profiles:
        await message.answer("Нет данных для отображения глобального рейтинга.")
        return
    sorted_users = sorted(user_profiles.items(), key=lambda item: item[1]["points"], reverse=True)
    text = "Глобальный топ пользователей по очкам:\n"
    for i, (uid, profile) in enumerate(sorted_users[:10], 1):
        text += f"{i}. {profile['username']} - {profile['points']} очков\n"
    await message.answer(text)

@dp.message_handler(commands=['points_global'])
async def cmd_points_global(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"Ваши глобальные очки: {profile['points']}")

@dp.message_handler(commands=['rose'])
async def cmd_rose(message: types.Message):
    args = message.get_args().split()
    if len(args) < 1:
        await message.answer("Используйте: /rose <user_id или username> [количество]")
        return
    try:
        amount = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        await message.answer("Неверное количество.")
        return
    sender_id = message.from_user.id
    _ = get_user_profile(sender_id, message.from_user.username or message.from_user.first_name)
    target_identifier = args[0]
    target_profile = None
    for uid, profile in user_profiles.items():
        if str(uid) == target_identifier or profile["username"] == target_identifier:
            target_profile = profile
            break
    if not target_profile:
        await message.answer("Целевой пользователь не найден.")
        return
    target_profile["roses"] += amount
    await message.answer(f"Вы отправили {amount} роз пользователю {target_profile['username']}.")

@dp.message_handler(commands=['voice_rose'])
async def cmd_voice_rose(message: types.Message):
    args = message.get_args().split()
    if len(args) < 1:
        await message.answer("Используйте: /voice_rose <user_id или username> [количество]")
        return
    try:
        amount = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        await message.answer("Неверное количество.")
        return
    sender_id = message.from_user.id
    _ = get_user_profile(sender_id, message.from_user.username or message.from_user.first_name)
    target_identifier = args[0]
    target_profile = None
    for uid, profile in user_profiles.items():
        if str(uid) == target_identifier or profile["username"] == target_identifier:
            target_profile = profile
            break
    if not target_profile:
        await message.answer("Целевой пользователь не найден.")
        return
    target_profile["roses"] += amount
    target_profile["points"] += amount
    await message.answer(f"Вы отправили {amount} голосовых роз пользователю {target_profile['username']}.")

@dp.message_handler(commands=['chats'])
async def cmd_chats(message: types.Message):
    if not chat_list:
        await message.answer("Список чатов пуст.")
        return
    text = "Список чатов:\n"
    for chat_id, info in chat_list.items():
        text += f"Chat ID: {chat_id}, Title: {info.get('title', 'Без названия')}\n"
    await message.answer(text)

@dp.message_handler(commands=['amount'])
async def cmd_amount(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"Ваш баланс: {profile['balance']}")

@dp.message_handler(commands=['roses_amount'])
async def cmd_roses_amount(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"У вас {profile['roses']} роз.")

@dp.message_handler(commands=['add_chat_to_list'])
async def cmd_add_chat_to_list(message: types.Message):
    user_id = message.from_user.id
    args = message.get_args().split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используйте: /add_chat_to_list <chat_id> <title>")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.answer("Неверный chat_id.")
        return
    title = args[1]
    chat_list[chat_id] = {"title": title, "link": None, "status": "не проверен", "score": 0}
    save_data()
    await message.answer(f"Чат {title} добавлен в список.")

@dp.message_handler(commands=['remove_chat_from_list'])
async def cmd_remove_chat_from_list(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    args = message.get_args().split()
    if not args:
        await message.answer("Используйте: /remove_chat_from_list <chat_id>")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.answer("Неверный chat_id.")
        return
    if chat_id in chat_list:
        del chat_list[chat_id]
        save_data()
        await message.answer("Чат удалён из списка.")
    else:
        await message.answer("Чат не найден.")

@dp.message_handler(commands=['update_chat_link'])
async def cmd_update_chat_link(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        await message.answer("Нет доступа.")
        return
    args = message.get_args().split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используйте: /update_chat_link <chat_id> <new_link>")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.answer("Неверный chat_id.")
        return
    new_link = args[1]
    if chat_id in chat_list:
        chat_list[chat_id]["link"] = new_link
        chat_list[chat_id]["status"] = "обновлено"
        save_data()
        await message.answer("Ссылка чата обновлена.")
    else:
        await message.answer("Чат не найден.")

@dp.message_handler(commands=['chat_link_status'])
async def cmd_chat_link_status(message: types.Message):
    args = message.get_args().split()
    if not args:
        await message.answer("Используйте: /chat_link_status <chat_id>")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.answer("Неверный chat_id.")
        return
    if chat_id in chat_list:
        status = chat_list[chat_id].get("status", "неизвестно")
        await message.answer(f"Статус ссылки чата: {status}")
    else:
        await message.answer("Чат не найден.")

@dp.message_handler(commands=['case'])
async def cmd_case(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    profile = get_user_profile(user_id, username)
    reward_type = random.choice(["balance", "points", "roses"])
    reward_amount = random.randint(1, 100)
    if reward_type == "balance":
        profile["balance"] += reward_amount
        save_data()
        await message.answer(f"Вы открыли кейс и получили {reward_amount} валюты!")
    elif reward_type == "points":
        profile["points"] += reward_amount
        save_data()
        await message.answer(f"Вы открыли кейс и получили {reward_amount} очков!")
    elif reward_type == "roses":
        profile["roses"] += reward_amount
        save_data()
        await message.answer(f"Вы открыли кейс и получили {reward_amount} роз!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
