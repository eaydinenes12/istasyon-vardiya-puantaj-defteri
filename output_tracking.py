from __future__ import annotations

from io import BytesIO
import json
import math
import zipfile


def stamp_output(content: bytes, extension: str, identity: dict) -> bytes:
    label = (
        f"Belge: {identity['id']} | {identity['created_at']} | "
        f"v{identity['app_version']} | Kaynak: {identity['source_code']}"
    )
    if identity.get("draft"):
        label = "TASLAK GORUNUM | " + label
    if extension == "jpeg":
        from PIL import Image, ImageDraw
        import reports

        with Image.open(BytesIO(content)) as original:
            original.load()
            image = Image.new("RGB", (max(640, original.width), original.height + 48), "white")
            image.paste(original.convert("RGB"), (0, 0))
        draw = ImageDraw.Draw(image)
        font_size = 14
        font = reports._font(font_size)
        while font_size > 7 and draw.textbbox((0, 0), label, font=font)[2] > image.width - 16:
            font_size -= 1
            font = reports._font(font_size)
        lines = [label]
        if draw.textbbox((0, 0), label, font=font)[2] > image.width - 16:
            lines = [label[:len(label) // 2], label[len(label) // 2:]]
        for index, line in enumerate(lines):
            draw.text((8, image.height - 42 + index * 18), line, fill="#3f4852", font=font)
        output = BytesIO()
        image.save(output, "JPEG", quality=95)
        return output.getvalue()
    if extension == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(content))
        workbook.properties.identifier = identity["id"]
        for sheet in workbook:
            previous = sheet.oddFooter.center.text or ""
            sheet.oddFooter.center.text = f"{previous}\n{label}".strip()
            sheet.oddFooter.center.size = 7
            sheet.evenFooter.center.text = sheet.oddFooter.center.text
            sheet.evenFooter.center.size = 7
            sheet.firstFooter.center.text = sheet.oddFooter.center.text
            sheet.firstFooter.center.size = 7
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()
    if extension == "pdf":
        from PIL import Image, ImageDraw
        from pypdf import PdfWriter
        import reports

        source_writer = PdfWriter(clone_from=BytesIO(content))
        writer = PdfWriter()
        for source in source_writer.pages:
            source.transfer_rotation_to_content()
            width, height = float(source.mediabox.width), float(source.mediabox.height)
            image = Image.new("RGB", (max(600, math.ceil(width * 2)), 48), "white")
            draw = ImageDraw.Draw(image)
            font_size = 14
            font = reports._font(font_size)
            while font_size > 7 and draw.textbbox((0, 0), label, font=font)[2] > image.width - 24:
                font_size -= 1
                font = reports._font(font_size)
            draw.text((12, 12), label, fill="#3f4852", font=font)
            footer_stream = BytesIO()
            image.save(footer_stream, format="PDF", resolution=144)
            footer_writer = PdfWriter(clone_from=BytesIO(footer_stream.getvalue()))
            footer = footer_writer.pages[0]
            footer.scale_to(width, 24)
            # A separate footer band leaves the original report content untouched.
            page = writer.add_blank_page(width=width, height=height + 24)
            page.merge_translated_page(source, 0, 24)
            page.merge_page(footer)
        writer.add_metadata({
            "/Title": identity["id"], "/Subject": label,
            "/Creator": f"Vardiya Puantaj {identity['app_version']}",
        })
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    if extension == "zip":
        output = BytesIO()
        with zipfile.ZipFile(BytesIO(content)) as original, zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED
        ) as stamped:
            for entry in original.infolist():
                body = original.read(entry.filename)
                suffix = entry.filename.rsplit(".", 1)[-1].lower()
                if suffix in ("xlsx", "pdf"):
                    body = stamp_output(body, suffix, identity)
                stamped.writestr(entry, body)
            stamped.writestr(
                "CIKTI_KIMLIGI.json",
                json.dumps(identity, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        return output.getvalue()
    return content
