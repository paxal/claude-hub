"""Pièces jointes : conversion des images au format accepté par l'API."""

from .gtk import GdkPixbuf


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")
API_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_SIDE, MAX_BYTES = 2000, 3_500_000


def prepare_image(raw):
    """Octets d'une image -> (media_type, octets) acceptés par l'API :
    format reconnu, côté ≤ 2000 px, taille raisonnable. Lève GLib.Error sinon."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(raw)
    loader.close()
    pb = loader.get_pixbuf()
    mime = (loader.get_format().get_mime_types() or [""])[0]
    w, h = pb.get_width(), pb.get_height()
    if mime in API_TYPES and max(w, h) <= MAX_SIDE and len(raw) <= MAX_BYTES:
        return mime, raw
    if max(w, h) > MAX_SIDE:
        k = MAX_SIDE / max(w, h)
        pb = pb.scale_simple(int(w * k), int(h * k), GdkPixbuf.InterpType.BILINEAR)
    ok, png = pb.save_to_bufferv("png", [], [])
    if len(png) <= MAX_BYTES:
        return "image/png", bytes(png)
    if pb.get_has_alpha():
        pb = pb.composite_color_simple(pb.get_width(), pb.get_height(),
                                       GdkPixbuf.InterpType.BILINEAR, 255, 1, 0xFFFFFF, 0xFFFFFF)
    ok, jpg = pb.save_to_bufferv("jpeg", ["quality"], ["85"])
    return "image/jpeg", bytes(jpg)
