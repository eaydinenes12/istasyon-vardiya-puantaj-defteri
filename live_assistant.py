from __future__ import annotations

import json
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import scheduling

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MODEL_NAME = re.compile(r"[A-Za-z0-9._:-]{1,120}")
MAX_MESSAGES = 12
MAX_MESSAGE_LENGTH = 4000
MAX_CONTEXT_LENGTH = 8000

SYSTEM_PROMPT = """Sen Vardiya Planı ve Puantaj uygulamasının Türkçe canlı asistanısın.
Kullanıcıya kısa, anlaşılır ve dürüst yanıt ver. Yalnızca verilen uygulama bağlamına dayan;
bilmediğin personel, tarih, vardiya veya puantaj bilgisini uydurma.
Kaynağı gösterilmeyen kişi, gün, toplam saat veya fazla mesai sayısı verme; uygulamanın hesap sonucu
bu sohbete sunulmadıysa erişimin olmadığını açıkça söyle.
Bir işlemin yapıldığını asla iddia etme. Değişiklikleri yalnızca aşağıdaki araçlardan biriyle
öner; uygulama kullanıcıdan ayrıca onay almadan hiçbir veriyi kaydetmez.
Silme, yedek geri yükleme, kullanıcı/yetki değişikliği, bordro veya dışa aktarım işlemi yapma.
Vardiya çalışma saati belirtilmemişse tahmin etme; netleştirici soru sor.
Vardiya veya puantaj değişikliği önermeden önce kullanıcı iletisinde tam personel adını ve açık takvim tarihini doğrula;
kod/tür ve çalışma saatlerini de kullanıcının açıkça belirttiği değerlerden seç. Emin değilsen araç çağırma, soru sor.
Puantaj kodları: X çalışma, H hafta tatili, G.Ç geceden çıkış, S yıllık izin,
R rapor, Ü ücretsiz izin, G işe gelmedi, İST. istifa, E evlilik izni,
K karantina, Y yarım gün. Kullanıcı anlamı belirsiz bir kod istediğinde önce sor.
Araç çağrısı yaparken yalnızca istenen tek işlemi hazırla; birden fazla işlemi tek seferde yapma."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate_view",
            "description": "Uygulamada güvenli biçimde bir bölümü aç.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {"type": "string", "enum": ["schedule", "attendance", "archive", "templates"]},
                },
                "required": ["view"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_backup",
            "description": "Kullanıcı onayından sonra bu bilgisayarda yedek oluştur.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_attendance_code",
            "description": "Bir personelin belirli tarihteki puantaj kodu için onay bekleyen değişiklik öner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_name": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "code": {"type": "string", "description": "Uygulamanın standart puantaj kodlarından biri"},
                },
                "required": ["employee_name", "date", "code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_schedule_day",
            "description": "Bir personelin bir günlük vardiyası için onay bekleyen değişiklik öner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_name": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "work", "weekly_off", "overnight_exit", "annual_leave", "sick_leave",
                            "unpaid_leave", "marriage_leave", "quarantine", "half_day", "absent",
                            "resignation", "custom",
                        ],
                    },
                    "start": {"type": "string", "description": "Çalışma başlangıcı HH:MM"},
                    "end": {"type": "string", "description": "Çalışma bitişi HH:MM"},
                    "custom_code": {"type": "string"},
                },
                "required": ["employee_name", "date", "kind"],
                "additionalProperties": False,
            },
        },
    },
]


class AssistantUnavailable(RuntimeError):
    pass


def _request_json(path: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{OLLAMA_BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read(2_000_001).decode("utf-8"))
    except HTTPError as exc:
        raise AssistantUnavailable(f"Yerel yapay zekâ hizmeti HTTP {exc.code} yanıtı verdi.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssistantUnavailable(
            "Yerel Ollama hizmetine erişilemiyor. Ollama'nın açık olduğunu kontrol edin."
        ) from exc
    if not isinstance(result, dict):
        raise AssistantUnavailable("Yerel yapay zekâ hizmetinden geçersiz yanıt alındı.")
    return result


def list_models() -> list[str]:
    result = _request_json("/api/tags")
    models = result.get("models")
    if not isinstance(models, list):
        raise AssistantUnavailable("Yerel model listesi okunamadı.")
    names = []
    for model in models:
        if isinstance(model, dict):
            name = model.get("name")
            if isinstance(name, str) and MODEL_NAME.fullmatch(name) and name not in names:
                names.append(name)
    return names


def _validate_messages(messages: object) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError("Sohbet geçmişi geçersiz; yeni bir sohbet başlatın.")
    cleaned = []
    total = 0
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ValueError("Sohbet iletisi geçersiz.")
        role, content = item["role"], item["content"]
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise ValueError("Sohbet iletisi geçersiz.")
        content = content.strip()
        if not content or len(content) > MAX_MESSAGE_LENGTH:
            raise ValueError("Her sohbet iletisi 1–4000 karakter olmalıdır.")
        total += len(content)
        if total > 16000:
            raise ValueError("Sohbet geçmişi çok uzun; yeni bir sohbet başlatın.")
        cleaned.append({"role": role, "content": content})
    if cleaned[-1]["role"] != "user":
        raise ValueError("Sohbet son iletisi kullanıcıdan gelmelidir.")
    return cleaned


def normalize_context(context: object) -> dict:
    allowed = {"view", "month", "people", "today"}
    if not isinstance(context, dict) or set(context) - allowed:
        raise ValueError("Asistan bağlamı geçersiz.")
    normalized = {}
    if "view" in context:
        view = context["view"]
        if not isinstance(view, str) or view not in {"schedule", "attendance", "archive", "templates"}:
            raise ValueError("Asistanın açık bölümü geçersiz.")
        normalized["view"] = view
    if "month" in context:
        normalized["month"] = scheduling.validate_month(context["month"])
    if "today" in context:
        normalized["today"] = _date(context["today"])
    if "people" in context:
        people = context["people"]
        if not isinstance(people, list) or len(people) > 60:
            raise ValueError("Asistan personel bağlamı geçersiz.")
        names = []
        for name in people:
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError("Asistan personel bağlamı geçersiz.")
            if name.strip() not in names:
                names.append(name.strip())
        normalized["people"] = names
    return normalized


def _date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
    if parsed.isoformat() != value:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.")
    return value


def _navigation_action(message: str) -> dict | None:
    command = message.casefold()
    if not re.search(
        r"\b(?:aç(?:ın|ar mısın|abilir misin)?|geç(?:in|er misin)?|git|göster|"
        r"görüntüle(?:r misin)?|open|go to|show)\b",
        command,
    ):
        return None
    sections = (
        (("puantaj", "attendance"), "attendance", "Puantaj"),
        (("vardiya", "schedule"), "schedule", "Vardiya"),
        (("arşiv", "arsiv", "archive"), "archive", "Arşiv"),
        (("şablon", "sablon", "template"), "templates", "Tablo şablonları"),
    )
    for terms, view, label in sections:
        if any(term in command for term in terms):
            return {"reply": f"{label} bölümü açılıyor.", "action": {"type": "navigate_view", "view": view}}
    return None


def _mentioned_dates(message: str, context: dict) -> set[str]:
    found = set()
    for raw in re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", message):
        try:
            found.add(_date(raw))
        except ValueError:
            pass
    for day, month, year in re.findall(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)", message):
        try:
            found.add(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            pass
    months = {
        "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
        "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
        "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
        "kasim": 11, "aralık": 12, "aralik": 12,
    }
    for day, month_name, year in re.findall(
        r"(?<!\d)(\d{1,2})\s+([a-zçğıöşü]+)(?:['’]?(?:da|de|ta|te))?(?:\s+(\d{4}))?(?!\d)",
        message.casefold(),
    ):
        month = months.get(month_name)
        if month is None:
            continue
        if year:
            target_year = int(year)
        elif isinstance(context.get("month"), str) and re.fullmatch(r"\d{4}-\d{2}", context["month"]):
            target_year = int(context["month"][:4])
        else:
            continue
        try:
            found.add(date(target_year, month, int(day)).isoformat())
        except ValueError:
            pass
    today_text = context.get("today")
    if isinstance(today_text, str):
        today = date.fromisoformat(today_text)
        weekdays = {
            "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
            "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
        }
        for phrase, weekday in weekdays.items():
            if re.search(rf"\b(?:gelecek|önümüzdeki|onumuzdeki)\s+{re.escape(phrase)}\b", message.casefold()):
                distance = (weekday - today.weekday()) % 7 or 7
                found.add(date.fromordinal(today.toordinal() + distance).isoformat())
    return found


def record_query_type(message: object) -> str | None:
    if not isinstance(message, str):
        return None
    text = message.casefold()
    if any(term in text for term in ("eksik vardiya", "vardiya açığı", "vardiya acigi", "eksik kadro", "kadro açığı", "kadro acigi")):
        return "staffing"
    if "plan" in text and any(term in text for term in (
        "oluşturamad", "olusturamad", "yapamad", "başarısız", "basarisiz", "çözülemedi", "cozulemedi",
    )):
        return "constraints"
    if any(term in text for term in ("çalışma saati", "calisma saati", "fazla mesai", "mesai toplam", "kaç saat", "kac saat")):
        return "calculation"
    return None


def _validate_action_request(action: dict, message: str, context: dict) -> str | None:
    if action["type"] not in {"set_attendance_code", "set_schedule_day"}:
        return None
    person = action["employee_name"].casefold()
    people = context.get("people", [])
    if action["employee_name"] not in people or person not in message.casefold():
        return "İşlem hazırlamak için uygulamadaki tam personel adını iletiniz."
    if action["date"] not in _mentioned_dates(message, context):
        return "Tarihi güvenle doğrulayamadım. Lütfen günü açıkça GG.AA.YYYY biçiminde belirtin."
    if action["type"] == "set_attendance_code":
        aliases = {
            "X": ("çalışma", "calisma", "çalıştı", "calisti"),
            "H": ("hafta tatili",),
            "G.Ç": ("geceden çıkış", "geceden cikis"),
            "S": ("yıllık izin", "yillik izin", "senelik izin"),
            "R": ("rapor",),
            "Ü": ("ücretsiz izin", "ucretsiz izin"),
            "G": ("işe gelmedi", "ise gelmedi", "devamsız", "devamsiz"),
            "İST.": ("istifa",),
            "E": ("evlilik izni",),
            "K": ("karantina",),
            "Y": ("yarım gün", "yarim gun"),
        }
        code = action["code"].casefold()
        token = re.search(rf"(?<!\w){re.escape(code)}(?!\w)", message.casefold())
        if not token and not any(term in message.casefold() for term in aliases[action["code"]]):
            return "Puantaj kodunu güvenle doğrulayamadım; istediğiniz kodu veya anlamını açıkça belirtin."
    else:
        kind_phrases = {
            "work": ("çalış", "calis", "mesai", "vardiya", "work"),
            "weekly_off": ("hafta tatili",),
            "overnight_exit": ("geceden çıkış", "geceden cikis", "gece çıkışı", "gece cikisi"),
            "annual_leave": ("yıllık izin", "yillik izin", "senelik izin"),
            "sick_leave": ("rapor", "hastalık izni", "hastalik izni"),
            "unpaid_leave": ("ücretsiz izin", "ucretsiz izin"),
            "marriage_leave": ("evlilik izni",),
            "quarantine": ("karantina",),
            "half_day": ("yarım gün", "yarim gun"),
            "absent": ("işe gelmedi", "ise gelmedi", "devamsız", "devamsiz"),
            "resignation": ("istifa",),
            "custom": ("özel vardiya", "ozel vardiya"),
        }
        if not any(term in message.casefold() for term in kind_phrases[action["kind"]]):
            return "Vardiya türünü güvenle doğrulayamadım; istediğiniz türü açıkça belirtin."
        if action["kind"] == "work":
            times = re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", message)
            if action["start"] not in times or action["end"] not in times:
                return "Çalışma saatlerini güvenle doğrulayamadım. Başlangıç ve bitişi SS:DD biçiminde yazın."
        elif action["kind"] == "custom":
            if action["custom_code"].casefold() not in message.casefold():
                return "Özel vardiya kodunu güvenle doğrulayamadım; kodu açıkça yazın."
    return None


def _action(tool_name: object, arguments: object) -> dict:
    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
        raise ValueError("Asistanın işlem önerisi anlaşılamadı.")
    if tool_name == "navigate_view":
        views = {"schedule", "attendance", "archive", "templates"}
        if set(arguments) != {"view"} or not isinstance(arguments["view"], str) or arguments["view"] not in views:
            raise ValueError("Açılacak uygulama bölümü geçersiz.")
        return {"type": "navigate_view", "view": arguments["view"]}
    if tool_name == "create_backup":
        if arguments:
            raise ValueError("Yedekleme isteği geçersiz.")
        return {"type": "create_backup"}
    if tool_name == "set_attendance_code":
        if set(arguments) != {"employee_name", "date", "code"}:
            raise ValueError("Puantaj değişikliği eksik veya geçersiz.")
        name, code = arguments["employee_name"], arguments["code"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Personel adı geçersiz.")
        if not isinstance(code, str):
            raise ValueError("Puantaj kodu geçersiz.")
        code = scheduling.normalize_attendance_code(code, allow_custom=False)
        if not code:
            raise ValueError("Boş puantaj kodu için uygulama içindeki hücre düzenlemesini kullanın.")
        return {
            "type": "set_attendance_code",
            "employee_name": name.strip(),
            "date": _date(arguments["date"]),
            "code": code,
        }
    if tool_name == "set_schedule_day":
        allowed = {"employee_name", "date", "kind", "start", "end", "custom_code"}
        required = {"employee_name", "date", "kind"}
        if not required <= set(arguments) or not set(arguments) <= allowed:
            raise ValueError("Vardiya değişikliği eksik veya geçersiz.")
        name, kind = arguments["employee_name"], arguments["kind"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Personel adı geçersiz.")
        kinds = {
            "work", "weekly_off", "overnight_exit", "annual_leave", "sick_leave",
            "unpaid_leave", "marriage_leave", "quarantine", "half_day", "absent",
            "resignation", "custom",
        }
        if not isinstance(kind, str) or kind not in kinds:
            raise ValueError("Vardiya türü geçersiz.")
        result = {"type": "set_schedule_day", "employee_name": name.strip(),
                  "date": _date(arguments["date"]), "kind": kind}
        if kind == "work":
            start, end = arguments.get("start"), arguments.get("end")
            if not isinstance(start, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start):
                raise ValueError("Çalışma başlangıç saati netleştirilmeli (SS:DD).")
            if not isinstance(end, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
                raise ValueError("Çalışma bitiş saati netleştirilmeli (SS:DD).")
            result.update({"start": start, "end": end})
        elif kind == "custom":
            code = arguments.get("custom_code")
            if not isinstance(code, str) or not re.fullmatch(r"[A-ZÇĞİÖŞÜ0-9. -]{1,8}", code.upper()):
                raise ValueError("Özel vardiya kodu netleştirilmeli.")
            result["custom_code"] = code.upper()
        return result
    raise ValueError("Bu işlem asistan tarafından desteklenmiyor.")


def chat(model: object, messages: object, context: object) -> dict:
    cleaned = _validate_messages(messages)
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
        raise ValueError("Yerel model seçimi geçersiz.")
    available = list_models()
    if model not in available:
        raise ValueError("Seçilen model kurulu değil. Yerel model listesini yenileyin.")
    navigation = _navigation_action(cleaned[-1]["content"])
    if navigation:
        return navigation
    normalized_context = normalize_context(context)
    context_text = json.dumps(normalized_context, ensure_ascii=False, separators=(",", ":"))
    if len(context_text) > MAX_CONTEXT_LENGTH:
        raise ValueError("Asistan bağlamı çok büyük.")
    system = SYSTEM_PROMPT + "\n\nGeçerli uygulama bağlamı:\n" + context_text
    response = _request_json(
        "/api/chat",
        {
            "model": model,
            "messages": [{"role": "system", "content": system}, *cleaned],
            "tools": TOOLS,
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=90,
    )
    message = response.get("message")
    if not isinstance(message, dict):
        raise AssistantUnavailable("Yerel model yanıtı geçersiz.")
    content = message.get("content", "")
    reply = content.strip() if isinstance(content, str) else ""
    tool_calls = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise AssistantUnavailable("Yerel modelin işlem önerisi geçersiz.")
    if not tool_calls:
        return {"reply": reply or "Bu isteği yanıtlayamadım; farklı biçimde sorabilir misiniz?"}
    if len(tool_calls) != 1 or not isinstance(tool_calls[0], dict):
        return {"reply": "Güvenli olması için tek seferde yalnızca bir işlem hazırlayabilirim."}
    function = tool_calls[0].get("function")
    if not isinstance(function, dict):
        return {"reply": "İşlem önerisini anlayamadım; isteği yeniden ifade edebilir misiniz?"}
    try:
        action = _action(function.get("name"), function.get("arguments"))
    except ValueError as exc:
        return {"reply": str(exc)}
    clarification = _validate_action_request(action, cleaned[-1]["content"], normalized_context)
    if clarification:
        return {"reply": clarification}
    return {
        "reply": reply or "İşlem önerisi hazır. Onaylamadan önce ayrıntılarını kontrol edin.",
        "action": action,
    }
