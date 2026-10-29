#!/usr/bin/python3
"""Snapmark für Omarchy: Bildschirmfoto aufnehmen und sofort beschriften.

  snapmark [smart|region|windows|fullscreen]   aufnehmen, dann Editor
  snapmark DATEI.png                           vorhandenes Bild bearbeiten
"""

import copy
import io
import math
import os
import subprocess
import sys
import tempfile
import time

MODES = ("smart", "region", "windows", "fullscreen")


def capture(mode):
    """Nimmt über Omarchys Auswahl (eingefrorener Bildschirm) auf, liefert den Pfad oder None."""
    tmp = tempfile.mkdtemp(prefix="snapmark-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    env = dict(os.environ, OMARCHY_SCREENSHOT_DIR=tmp)
    out = subprocess.run(["omarchy-capture-screenshot", mode, "save"],
                         env=env, capture_output=True, text=True).stdout.strip()
    path = out.splitlines()[-1] if out else ""
    return path if path and os.path.isfile(path) else None


def target_dir():
    pics = os.path.expanduser("~/Pictures")
    try:
        with open(os.path.expanduser("~/.config/user-dirs.dirs")) as f:
            for line in f:
                if line.startswith("XDG_PICTURES_DIR="):
                    pics = os.path.expandvars(line.split("=", 1)[1].strip().strip('"'))
    except OSError:
        pass
    return os.environ.get("OMARCHY_SCREENSHOT_DIR", pics)


args = sys.argv[1:]
if args and args[0] in ("-h", "--help"):
    print(__doc__.strip())
    sys.exit(0)
if not args or args[0] in MODES:
    SOURCE = capture(args[0] if args else "smart")
    if not SOURCE:
        sys.exit(0)
    SAVE_PATH = None  # neues Bild: erst beim Speichern ein Name im Bilderordner
else:
    SOURCE = os.path.abspath(args[0])
    SAVE_PATH = SOURCE

import gi  # noqa: E402  erst nach der Aufnahme laden, damit die Auswahl sofort kommt

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

RED = (0.89, 0.11, 0.14, 1.0)
WHITE = (1.0, 1.0, 1.0, 1.0)
YELLOW = (1.0, 0.93, 0.0, 1.0)

# Werkzeug: (Schlüssel, Name, Taste)
TOOLS = [
    ("select", "Auswählen", "Escape"),
    ("rect", "Rechteck", "r"),
    ("ellipse", "Ellipse", "e"),
    ("line", "Linie", "l"),
    ("arrow", "Pfeil", "a"),
    ("freehand", "Freihand", "f"),
    ("text", "Text", "t"),
    ("highlight", "Markieren", "h"),
    ("obfuscate", "Unkenntlich machen", "o"),
    ("counter", "Zähler", "i"),
    ("crop", "Zuschneiden", "c"),
]

# Vorgaben je Werkzeug; Änderungen in der Leiste bleiben für dieses Werkzeug bestehen.
DEFAULTS = {
    "rect": dict(stroke=RED, fill=None, width=3),
    "ellipse": dict(stroke=RED, fill=None, width=3),
    "line": dict(stroke=RED, width=3),
    "arrow": dict(stroke=RED, width=3),
    "freehand": dict(stroke=RED, width=3),
    "text": dict(stroke=RED, fill=None, size=24),
    "highlight": dict(stroke=YELLOW),
    "obfuscate": dict(size=12),
    "counter": dict(stroke=RED, size=16),
}
BOX_KINDS = ("rect", "ellipse", "highlight", "obfuscate")
SIZE_LABEL = {"text": "Schrift", "counter": "Größe", "obfuscate": "Pixel"}
HANDLE = 5  # Griffgröße in Bildschirmpunkten


class Obj:
    def __init__(self, kind, pts, **props):
        self.kind = kind
        self.pts = pts  # Bildkoordinaten
        self.stroke = props.get("stroke")
        self.fill = props.get("fill")
        self.width = props.get("width", 0)
        self.size = props.get("size", 0)
        self.text = ""
        self.num = 0

    def box(self):
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return min(xs), min(ys), max(xs), max(ys)

    def move(self, dx, dy):
        for p in self.pts:
            p[0] += dx
            p[1] += dy


def text_layout(cr, obj):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string("Sans Bold")
    desc.set_absolute_size(obj.size * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(obj.text or " ", -1)
    return layout


def text_bounds(obj):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    _, logical = text_layout(cairo.Context(surf), obj).get_pixel_extents()
    pad = obj.size * 0.3
    x, y = obj.pts[0]
    return x - pad, y - pad, x + logical.width + pad, y + logical.height + pad


def bounds(obj):
    """Umriss eines Objekts in Bildkoordinaten, für Treffer und Griffe."""
    if obj.kind == "text":
        return text_bounds(obj)
    if obj.kind == "counter":
        x, y = obj.pts[0]
        return x - obj.size, y - obj.size, x + obj.size, y + obj.size
    x1, y1, x2, y2 = obj.box()
    m = obj.width / 2
    return x1 - m, y1 - m, x2 + m, y2 + m


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def hit(obj, p, tol):
    if obj.kind in ("line", "arrow", "freehand"):
        pts = obj.pts
        return any(seg_dist(p, pts[i], pts[i + 1]) <= tol + obj.width / 2 for i in range(len(pts) - 1)) \
            or (len(pts) == 1 and math.dist(p, pts[0]) <= tol)
    if obj.kind == "counter":
        return math.dist(p, obj.pts[0]) <= obj.size + tol
    x1, y1, x2, y2 = bounds(obj)
    if not (x1 - tol <= p[0] <= x2 + tol and y1 - tol <= p[1] <= y2 + tol):
        return False
    if obj.kind in ("rect", "ellipse") and obj.fill is None:
        # leere Formen nur am Rand greifen, damit Darunterliegendes erreichbar bleibt
        inner = tol + obj.width
        return not (x1 + inner < p[0] < x2 - inner and y1 + inner < p[1] < y2 - inner)
    return True


def draw_arrow_head(cr, a, b, width):
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    length = max(12, width * 4)
    spread = math.radians(25)
    cr.move_to(*b)
    cr.line_to(b[0] - length * math.cos(ang - spread), b[1] - length * math.sin(ang - spread))
    cr.line_to(b[0] - length * math.cos(ang + spread), b[1] - length * math.sin(ang + spread))
    cr.close_path()
    cr.fill()


def pixelate(cr, comp, obj):
    x1, y1, x2, y2 = (int(round(v)) for v in obj.box())
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(comp.get_width(), x2), min(comp.get_height(), y2)
    w, h, px = x2 - x1, y2 - y1, max(2, int(obj.size))
    if w < 1 or h < 1:
        return
    comp.flush()
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil(w / px), math.ceil(h / px))
    sc = cairo.Context(small)
    sc.scale(1 / px, 1 / px)
    sc.set_source_surface(comp, -x1, -y1)
    sc.get_source().set_filter(cairo.FILTER_GOOD)
    sc.paint()
    cr.save()
    cr.rectangle(x1, y1, w, h)
    cr.clip()
    cr.translate(x1, y1)
    cr.scale(px, px)
    cr.set_source_surface(small, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)
    cr.paint()
    cr.restore()


def draw_obj(cr, comp, obj):
    k = obj.kind
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if k == "obfuscate":
        pixelate(cr, comp, obj)
    elif k == "highlight":
        x1, y1, x2, y2 = obj.box()
        cr.save()
        cr.set_operator(cairo.OPERATOR_MULTIPLY)
        cr.set_source_rgba(*obj.stroke)
        cr.rectangle(x1, y1, x2 - x1, y2 - y1)
        cr.fill()
        cr.restore()
    elif k in ("rect", "ellipse"):
        x1, y1, x2, y2 = obj.box()
        if k == "rect":
            cr.rectangle(x1, y1, x2 - x1, y2 - y1)
        else:
            cr.save()
            cr.translate((x1 + x2) / 2, (y1 + y2) / 2)
            cr.scale(max(x2 - x1, 1) / 2, max(y2 - y1, 1) / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
        if obj.fill:
            cr.set_source_rgba(*obj.fill)
            cr.fill_preserve()
        cr.set_source_rgba(*obj.stroke)
        cr.set_line_width(obj.width)
        cr.stroke()
    elif k in ("line", "arrow", "freehand"):
        cr.set_source_rgba(*obj.stroke)
        cr.set_line_width(obj.width)
        pts = obj.pts
        a, b = pts[0], pts[-1]
        if k == "arrow" and math.dist(a, b) > 1:
            # Linie vor der Spitze enden lassen, sonst ragt sie bei dicken Strichen heraus
            length = max(12, obj.width * 4) * 0.8
            ang = math.atan2(b[1] - a[1], b[0] - a[0])
            cr.move_to(*a)
            cr.line_to(b[0] - length * math.cos(ang), b[1] - length * math.sin(ang))
            cr.stroke()
            draw_arrow_head(cr, a, b, obj.width)
        else:
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            if len(pts) == 1:
                cr.line_to(pts[0][0] + 0.1, pts[0][1])
            cr.stroke()
    elif k == "counter":
        x, y = obj.pts[0]
        r = obj.size
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.set_source_rgba(*obj.stroke)
        cr.fill_preserve()
        cr.set_source_rgba(*WHITE)
        cr.set_line_width(max(1.5, r / 8))
        cr.stroke()
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string("Sans Bold")
        desc.set_absolute_size(r * 1.1 * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(str(obj.num), -1)
        _, ext = layout.get_pixel_extents()
        r_, g_, b_, _ = obj.stroke
        dark = 0.299 * r_ + 0.587 * g_ + 0.114 * b_ > 0.6
        cr.set_source_rgba(*((0, 0, 0, 1) if dark else WHITE))
        cr.move_to(x - ext.width / 2 - ext.x, y - ext.height / 2 - ext.y)
        PangoCairo.show_layout(cr, layout)
    elif k == "text":
        x1, y1, x2, y2 = text_bounds(obj)
        if obj.fill:
            cr.set_source_rgba(*obj.fill)
            cr.rectangle(x1, y1, x2 - x1, y2 - y1)
            cr.fill()
        layout = text_layout(cr, obj)
        if not obj.text:
            return
        cr.set_source_rgba(*obj.stroke)
        cr.move_to(*obj.pts[0])
        PangoCairo.show_layout(cr, layout)


ACCENT = (0.13, 0.45, 0.95, 1.0)


def halo_stroke(cr, color, width, halo=(1, 1, 1, 0.95)):
    """Linie mit Saum in Gegenfarbe: bleibt auf hellem wie dunklem Bildinhalt sichtbar."""
    cr.set_source_rgba(*halo)
    cr.set_line_width(width + 2.5)
    cr.stroke_preserve()
    cr.set_source_rgba(*color)
    cr.set_line_width(width)
    cr.stroke()


def draw_handle(cr, x, y):
    cr.rectangle(round(x) - HANDLE + 0.5, round(y) - HANDLE + 0.5, HANDLE * 2, HANDLE * 2)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.set_line_width(3)
    cr.stroke_preserve()
    cr.set_source_rgba(*ACCENT)
    cr.set_line_width(1.5)
    cr.stroke()


def rgba_to_tuple(c):
    return (c.red, c.green, c.blue, c.alpha)


def tuple_to_rgba(t):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = t
    return c


PALETTE = [
    RED, (0.96, 0.47, 0.0, 1.0), YELLOW, (0.18, 0.72, 0.29, 1.0), (0.11, 0.45, 0.91, 1.0),
    (0.57, 0.25, 0.84, 1.0), (0.0, 0.0, 0.0, 1.0), (0.5, 0.5, 0.5, 1.0), WHITE,
]

CSS = """
.palette { padding: 6px; border-radius: 12px; border: 1px solid color-mix(in srgb, currentColor 18%, transparent); }
.palette button.tool { min-width: 36px; min-height: 36px; padding: 0; border-radius: 8px; color: var(--window-fg-color); }
.palette button.tool:hover { background: color-mix(in srgb, currentColor 12%, transparent); }
.palette button.tool:checked { background: var(--accent-bg-color); color: var(--accent-fg-color); }
.propbar { padding: 6px 12px; border-bottom: 1px solid color-mix(in srgb, currentColor 18%, transparent); }
.prop-label { font-weight: 600; }
.hint { opacity: 0.75; }
.backdrop-canvas { background: color-mix(in srgb, var(--window-bg-color) 72%, black); }
.page { box-shadow: 0 0 0 1px rgba(0,0,0,0.35), 0 4px 18px rgba(0,0,0,0.40); }
.swatch { min-width: 28px; min-height: 28px; padding: 2px; border-radius: 999px; }
.swatch:hover { background: color-mix(in srgb, currentColor 14%, transparent); }
.statusbar { padding: 2px 8px; border-top: 1px solid color-mix(in srgb, currentColor 18%, transparent); }
"""


def paint_swatch(cr, w, h, color):
    r = min(w, h) / 2 - 1
    cx, cy = w / 2, h / 2
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.set_source_rgba(*(color or WHITE))
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.45)
    cr.set_line_width(1.2)
    cr.stroke()
    if color is None:  # „keine Füllung“: durchgestrichen
        d = r * 0.7
        cr.move_to(cx - d, cy + d)
        cr.line_to(cx + d, cy - d)
        cr.set_source_rgba(*RED)
        cr.set_line_width(2)
        cr.stroke()


def swatch(color, size):
    area = Gtk.DrawingArea(content_width=size, content_height=size)
    area.color = color
    area.set_draw_func(lambda a, cr, w, h: paint_swatch(cr, w, h, a.color))
    return area


class ColorPicker(Gtk.MenuButton):
    """Farbknopf mit Vorgabefarben im Aufklappmenü, optional „keine“."""

    def __init__(self, on_change, allow_none=False):
        super().__init__(tooltip_text="Farbe wählen")
        self.add_css_class("flat")
        self.on_change = on_change
        self.color = RED
        self.preview = swatch(self.color, 20)
        self.set_child(self.preview)

        grid = Gtk.Grid(column_spacing=4, row_spacing=4, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        colors = PALETTE + ([None] if allow_none else [])
        for i, c in enumerate(colors):
            b = Gtk.Button(child=swatch(c, 22), tooltip_text="Keine" if c is None else None)
            b.add_css_class("flat")
            b.add_css_class("swatch")
            b.connect("clicked", lambda _b, c=c: self.pick(c))
            grid.attach(b, i % 5, i // 5, 1, 1)
        custom = Gtk.Button(label="Eigene Farbe …", margin_top=4)
        custom.add_css_class("flat")
        custom.connect("clicked", self.custom)
        grid.attach(custom, 0, 2, 5, 1)
        self.set_popover(Gtk.Popover(child=grid))

    def set_color(self, color):
        self.color = color
        self.preview.color = color
        self.preview.queue_draw()

    def pick(self, color):
        self.get_popover().popdown()
        self.set_color(color)
        self.on_change()

    def custom(self, _btn):
        self.get_popover().popdown()

        def done(dlg, res):
            try:
                c = dlg.choose_rgba_finish(res)
            except GLib.Error:
                return
            self.pick(rgba_to_tuple(c))
        Gtk.ColorDialog(with_alpha=True).choose_rgba(self.get_root(), tuple_to_rgba(self.color or WHITE), None, done)


def draw_icon(cr, kind, size):
    """Werkzeugsymbole, gezeichnet auf einem 16er-Raster in der Textfarbe."""
    cr.scale(size / 16, size / 16)
    cr.set_line_width(1.6)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "select":
        for x, y in ((4, 2), (4, 13.5), (7, 10.8), (9.3, 15), (11, 14.2), (8.8, 10.2), (12.4, 10.2)):
            cr.line_to(x, y)
        cr.close_path()
        cr.fill()
    elif kind == "rect":
        cr.rectangle(2.5, 4, 11, 8)
        cr.stroke()
    elif kind == "ellipse":
        cr.save()
        cr.translate(8, 8)
        cr.scale(6, 4.8)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()
        cr.stroke()
    elif kind == "line":
        cr.move_to(3, 13)
        cr.line_to(13, 3)
        cr.stroke()
    elif kind == "arrow":
        cr.move_to(3, 13)
        cr.line_to(10.5, 5.5)
        cr.stroke()
        draw_arrow_head(cr, (3, 13), (13.5, 2.5), 1.6)
    elif kind == "freehand":
        cr.move_to(2, 11)
        cr.curve_to(5, 2, 7, 15, 10, 8)
        cr.curve_to(11.5, 4.5, 13, 6, 14, 4.5)
        cr.stroke()
    elif kind == "text":
        cr.rectangle(3, 2.5, 10, 2.2)
        cr.rectangle(6.9, 2.5, 2.2, 11)
        cr.fill()
    elif kind == "highlight":
        r, g, b, a = cr.get_source().get_rgba()
        cr.set_source_rgba(r, g, b, a * 0.4)
        cr.rectangle(1.5, 6, 13, 4.5)
        cr.fill()
        cr.set_source_rgba(r, g, b, a)
        for y in (3, 8.2, 13):
            cr.move_to(3, y)
            cr.line_to(13 if y != 13 else 9, y)
        cr.stroke()
    elif kind == "obfuscate":
        r, g, b, a = cr.get_source().get_rgba()
        for i in range(4):
            for j in range(4):
                cr.set_source_rgba(r, g, b, a * (1.0 if (i + j) % 2 == 0 else 0.35))
                cr.rectangle(2 + i * 3, 2 + j * 3, 3, 3)
                cr.fill()
    elif kind == "counter":
        cr.arc(8, 8, 6.2, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(6.6, 6)
        cr.line_to(8.4, 4.8)
        cr.line_to(8.4, 11.2)
        cr.move_to(6.6, 11.2)
        cr.line_to(10.2, 11.2)
        cr.stroke()
    elif kind == "crop":
        cr.move_to(4.5, 1.5)
        cr.line_to(4.5, 11.5)
        cr.line_to(14.5, 11.5)
        cr.move_to(1.5, 4.5)
        cr.line_to(11.5, 4.5)
        cr.line_to(11.5, 14.5)
        cr.stroke()


def tool_icon(kind):
    area = Gtk.DrawingArea(content_width=20, content_height=20, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

    def draw(a, cr, w, h):
        cr.set_source_rgba(*rgba_to_tuple(a.get_color()))
        draw_icon(cr, kind, min(w, h))
    area.set_draw_func(draw)
    return area


class Editor(Adw.ApplicationWindow):
    def __init__(self, app, path):
        super().__init__(application=app, title="Snapmark")
        self.base = cairo.ImageSurface.create_from_png(path)
        self.objs = []
        self.undo_stack, self.redo_stack = [], []
        self.defaults = copy.deepcopy(DEFAULTS)
        self.tool = "select"
        self.selected = None
        self.editing = None  # Textobjekt, in das gerade getippt wird
        self.crop = None
        self.drag = None
        self.comp = None
        self.dirty = True
        self.changed = False
        self.save_path = SAVE_PATH
        self.zoom = 1.0
        self.syncing = False

        self.canvas = Gtk.DrawingArea(focusable=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                      margin_start=28, margin_end=28, margin_top=28, margin_bottom=28)
        self.canvas.add_css_class("page")
        self.canvas.set_draw_func(self.on_draw)
        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self.canvas)
        self.scroller.add_css_class("backdrop-canvas")

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.canvas.add_controller(drag)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self.on_click)
        self.canvas.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        self.canvas.add_controller(motion)

        self.im = Gtk.IMMulticontext()
        self.im.set_client_widget(self.canvas)
        self.im.connect("commit", self.on_im_commit)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        save_as = Gio.SimpleAction(name="save-as")
        save_as.connect("activate", lambda *_: self.save_as())
        self.add_action(save_as)

        self.toasts = Adw.ToastOverlay(child=self.scroller)
        body = Gtk.Box()
        body.append(self.build_tools())
        body.append(self.toasts)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(self.build_header())
        view.add_top_bar(self.build_props())
        view.add_bottom_bar(self.build_status())
        self.set_content(view)

        self.connect("close-request", self.on_close_request)
        self.set_tool("select")
        self.fit_initial()

    # ---------- Oberfläche ----------

    def build_header(self):
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Snapmark")
        header.set_title_widget(self.title)
        for icon, tip, cb in (("edit-undo-symbolic", "Rückgängig (Strg+Z)", self.undo),
                              ("edit-redo-symbolic", "Wiederholen (Strg+Y)", self.redo)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda *_, f=cb: f())
            header.pack_start(b)

        menu = Gio.Menu()
        menu.append("Speichern unter …", "win.save-as")
        save = Adw.SplitButton(label="Speichern", menu_model=menu, tooltip_text="Im Bilderordner speichern (Strg+S)")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self.save())
        header.pack_end(save)
        copy_btn = Gtk.Button(child=Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Kopieren"),
                              tooltip_text="In die Zwischenablage (Strg+C)")
        copy_btn.connect("clicked", lambda *_: self.copy_clipboard())
        header.pack_end(copy_btn)
        return header

    def build_tools(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.START,
                      margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        box.add_css_class("card")
        box.add_css_class("palette")
        self.tool_buttons = {}
        group = None
        for key, name, accel in TOOLS:
            if key in ("rect", "text", "crop"):
                box.append(Gtk.Separator(margin_top=3, margin_bottom=3))
            btn = Gtk.ToggleButton(child=tool_icon(key),
                                   tooltip_text=f"{name} ({'Esc' if accel == 'Escape' else accel.upper()})")
            btn.add_css_class("flat")
            btn.add_css_class("tool")
            if group:
                btn.set_group(group)
            group = group or btn
            btn.connect("toggled", lambda b, k=key: b.get_active() and self.set_tool(k))
            box.append(btn)
            self.tool_buttons[key] = btn
        return box

    def build_props(self):
        bar = Gtk.Box(spacing=18)
        bar.add_css_class("propbar")

        def labeled(text, widget):
            b = Gtk.Box(spacing=6)
            label = Gtk.Label(label=text)
            label.add_css_class("prop-label")
            b.append(label)
            b.append(widget)
            bar.append(b)
            return b, label

        self.stroke_btn = ColorPicker(lambda: self.prop_changed("stroke"))
        self.stroke_box, _ = labeled("Farbe", self.stroke_btn)
        self.fill_btn = ColorPicker(lambda: self.prop_changed("fill"), allow_none=True)
        self.fill_btn.set_color(None)
        self.fill_box, _ = labeled("Füllung", self.fill_btn)
        self.width_spin = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.width_spin.connect("value-changed", lambda *_: self.prop_changed("width"))
        self.width_box, _ = labeled("Linie", self.width_spin)
        self.size_spin = Gtk.SpinButton.new_with_range(4, 200, 1)
        self.size_spin.connect("value-changed", lambda *_: self.prop_changed("size"))
        self.size_box, self.size_label = labeled("Größe", self.size_spin)
        self.hint = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.hint.add_css_class("hint")
        bar.append(self.hint)
        return bar

    def build_status(self):
        bar = Gtk.Box(spacing=4)
        bar.add_css_class("statusbar")
        self.size_info = Gtk.Label(xalign=0, hexpand=True)
        self.size_info.add_css_class("hint")
        bar.append(self.size_info)
        for icon, tip, cb in (
            ("zoom-out-symbolic", "Verkleinern (Strg+Minus)", lambda: self.set_zoom(self.zoom / 1.25)),
            (None, "Originalgröße (Strg+0)", lambda: self.set_zoom(1 / self.get_scale_factor())),
            ("zoom-in-symbolic", "Vergrößern (Strg+Plus)", lambda: self.set_zoom(self.zoom * 1.25)),
            ("zoom-fit-best-symbolic", "Einpassen", self.fit),
        ):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip) if icon else Gtk.Button(tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda *_, f=cb: f())
            if not icon:
                self.zoom_btn = b
                b.set_size_request(64, -1)
            bar.append(b)
        return bar

    def sync_props(self):
        """Leiste an Werkzeug bzw. ausgewähltes Objekt anpassen."""
        src = self.selected
        kind = src.kind if src else self.tool
        d = self.defaults.get(kind) or {}
        vals = dict(stroke=src.stroke, fill=src.fill, width=src.width, size=src.size) if src else d
        self.syncing = True
        self.stroke_box.set_visible("stroke" in d)
        self.fill_box.set_visible("fill" in d)
        self.width_box.set_visible("width" in d)
        self.size_box.set_visible("size" in d)
        if vals.get("stroke"):
            self.stroke_btn.set_color(vals["stroke"])
        if "fill" in d:
            self.fill_btn.set_color(vals.get("fill"))
        if vals.get("width"):
            self.width_spin.set_value(vals["width"])
        if vals.get("size"):
            self.size_spin.set_value(vals["size"])
        self.size_label.set_label(SIZE_LABEL.get(kind, "Größe"))
        self.syncing = False
        self.update_status()

    def prop_changed(self, name):
        if self.syncing:
            return
        value = {
            "stroke": lambda: self.stroke_btn.color,
            "fill": lambda: self.fill_btn.color,
            "width": lambda: self.width_spin.get_value(),
            "size": lambda: self.size_spin.get_value(),
        }[name]()
        kind = self.selected.kind if self.selected else self.tool
        if kind in self.defaults and name in self.defaults[kind]:
            self.defaults[kind][name] = value
        if self.selected and getattr(self.selected, name) != value:
            self.push_undo()
            setattr(self.selected, name, value)
            self.touch()

    def set_tool(self, tool):
        self.finish_text()
        if tool != "crop":
            self.crop = None
        self.tool = tool
        if not self.tool_buttons[tool].get_active():
            self.tool_buttons[tool].set_active(True)
        if tool != "select":
            self.selected = None
        self.canvas.set_cursor_from_name("default" if tool == "select" else "crosshair")
        self.sync_props()
        self.canvas.queue_draw()

    def update_status(self):
        w, h = self.base.get_width(), self.base.get_height()
        if self.tool == "select" and not self.selected:
            hint = "Klicken = auswählen, ziehen = verschieben, Entf = löschen"
        elif self.tool == "select":
            hint = ""
        else:
            hint = {
                "crop": "Bereich ziehen, Enter = zuschneiden, Esc = abbrechen",
                "text": "Klicken und tippen, Esc = fertig",
                "counter": "Klicken setzt die nächste Nummer",
                "obfuscate": "Bereich ziehen, der verpixelt wird",
            }.get(self.tool, "Ziehen zum Zeichnen, Umschalt = gerade bzw. quadratisch")
        self.hint.set_label(hint)
        name = os.path.basename(self.save_path) if self.save_path else "Neue Aufnahme"
        self.title.set_subtitle(name + (" •" if self.changed else ""))
        self.size_info.set_label(f"{w} × {h} px")
        self.zoom_btn.set_label(f"{round(self.zoom * 100)} %")

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=2))

    def fit_initial(self):
        mons = Gdk.Display.get_default().get_monitors()
        mon = mons.get_item(0) if mons.get_n_items() else None
        w, h = self.base.get_width(), self.base.get_height()
        zoom = 1 / (mon.get_scale_factor() if mon else 1)
        if mon:
            geo = mon.get_geometry()
            chrome_w, chrome_h = 130, 200
            zoom = min(zoom, (geo.width * 0.85 - chrome_w) / w, (geo.height * 0.85 - chrome_h) / h)
            self.set_default_size(int(max(760, w * zoom + chrome_w)), int(max(520, h * zoom + chrome_h)))
        self.set_zoom(zoom)

    def fit(self):
        w = self.scroller.get_width() - 56
        h = self.scroller.get_height() - 56
        if w > 0 and h > 0:
            self.set_zoom(min(w / self.base.get_width(), h / self.base.get_height()))

    def set_zoom(self, zoom):
        self.zoom = max(0.05, min(8.0, zoom))
        self.canvas.set_content_width(math.ceil(self.base.get_width() * self.zoom))
        self.canvas.set_content_height(math.ceil(self.base.get_height() * self.zoom))
        self.update_status()
        self.canvas.queue_draw()

    # ---------- Zeichnen ----------

    def touch(self):
        self.dirty = True
        if not self.changed:
            self.changed = True
            self.update_status()
        self.canvas.queue_draw()

    def composite(self):
        if self.dirty or self.comp is None:
            comp = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.base.get_width(), self.base.get_height())
            cr = cairo.Context(comp)
            cr.set_source_surface(self.base, 0, 0)
            cr.paint()
            for obj in self.objs:
                draw_obj(cr, comp, obj)
            comp.flush()
            self.comp = comp
            self.dirty = False
        return self.comp

    def on_draw(self, area, cr, width, height):
        comp = self.composite()
        cr.save()
        cr.scale(self.zoom, self.zoom)
        cr.set_source_surface(comp, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD if self.zoom < 1 else cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()
        z = self.zoom

        if self.crop:
            x1, y1, x2, y2 = self.norm(self.crop)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.rectangle(0, 0, width, height)
            cr.rectangle(x1 * z, y1 * z, (x2 - x1) * z, (y2 - y1) * z)
            cr.set_source_rgba(0, 0, 0, 0.62)
            cr.fill()
            cr.rectangle(x1 * z + 0.5, y1 * z + 0.5, (x2 - x1) * z, (y2 - y1) * z)
            halo_stroke(cr, WHITE, 1.5, (0, 0, 0, 0.7))
            for hx, hy in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
                draw_handle(cr, hx * z, hy * z)

        if self.selected:
            x1, y1, x2, y2 = bounds(self.selected)
            cr.rectangle(round(x1 * z) - 3.5, round(y1 * z) - 3.5, round((x2 - x1) * z) + 7, round((y2 - y1) * z) + 7)
            cr.set_dash([5, 3])
            halo_stroke(cr, ACCENT, 1.5)
            cr.set_dash([])
            for hx, hy in self.handles(self.selected):
                draw_handle(cr, hx * z, hy * z)

        if self.editing:
            # Schreibmarke am Textende
            ob = self.editing
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            layout = text_layout(cairo.Context(surf), ob)
            if not ob.text:
                layout.set_text("", -1)
            pos, _ = layout.get_cursor_pos(len(ob.text.encode()))
            x = (ob.pts[0][0] + pos.x / Pango.SCALE) * z
            y = (ob.pts[0][1] + pos.y / Pango.SCALE) * z
            h = max(pos.height / Pango.SCALE, ob.size) * z
            cr.move_to(x + 1, y)
            cr.line_to(x + 1, y + h)
            halo_stroke(cr, ob.stroke, max(1.5, z * 2))

    @staticmethod
    def norm(pts):
        (ax, ay), (bx, by) = pts
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)

    def handles(self, obj):
        if obj.kind in ("line", "arrow"):
            return [tuple(p) for p in obj.pts]
        if obj.kind in BOX_KINDS:
            x1, y1, x2, y2 = obj.box()
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return []

    # ---------- Maus ----------

    def to_img(self, x, y):
        return [x / self.zoom, y / self.zoom]

    def hit_object(self, p):
        tol = 4 / self.zoom
        for obj in reversed(self.objs):
            if hit(obj, p, tol):
                return obj
        return None

    def hit_handle(self, p):
        if not self.selected:
            return None
        r = (HANDLE + 2) / self.zoom
        for i, (hx, hy) in enumerate(self.handles(self.selected)):
            if abs(p[0] - hx) <= r and abs(p[1] - hy) <= r:
                return i
        return None

    def on_click(self, gesture, n_press, x, y):
        self.canvas.grab_focus()
        if n_press == 2:
            obj = self.hit_object(self.to_img(x, y))
            if obj and obj.kind == "text":
                self.start_text(obj)
            elif self.tool == "crop" and self.crop:
                self.apply_crop()

    def on_motion(self, ctrl, x, y):
        if self.tool != "select" or self.drag:
            return
        p = self.to_img(x, y)
        if self.hit_handle(p) is not None:
            name = "nwse-resize"
        elif self.hit_object(p):
            name = "move"
        else:
            name = "default"
        self.canvas.set_cursor_from_name(name)

    def on_drag_begin(self, gesture, x, y):
        p = self.to_img(x, y)
        shift = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        self.drag = dict(start=p, last=p, shift=shift, moved=False)

        if self.editing:
            if hit(self.editing, p, 4 / self.zoom):
                self.drag["mode"] = "none"
                return
            self.finish_text()

        if self.tool == "select":
            i = self.hit_handle(p)
            if i is not None:
                self.push_undo()
                if self.selected.kind in BOX_KINDS:
                    x1, y1, x2, y2 = self.selected.box()
                    self.selected.pts = [[x1, y1], [x2, y2]]
                self.drag.update(mode="resize", handle=i)
                return
            self.selected = self.hit_object(p)
            self.sync_props()
            if self.selected:
                self.push_undo()
                self.drag["mode"] = "move"
            else:
                self.drag["mode"] = "none"
            self.canvas.queue_draw()
            return

        if self.tool == "crop":
            self.crop = [p, list(p)]
            self.drag["mode"] = "crop"
            return

        if self.tool == "text":
            obj = self.hit_object(p)
            if obj and obj.kind == "text":
                self.start_text(obj)
                self.drag["mode"] = "none"
                return

        d = self.defaults[self.tool]
        self.push_undo()
        if self.tool in ("counter", "text"):
            obj = Obj(self.tool, [list(p)], **d)
            if self.tool == "counter":
                obj.num = 1 + max((o.num for o in self.objs if o.kind == "counter"), default=0)
            self.objs.append(obj)
            self.selected = obj
            self.drag["mode"] = "move"
            if self.tool == "text":
                self.start_text(obj, new=True)
            self.touch()
            return
        pts = [list(p)] if self.tool == "freehand" else [list(p), list(p)]
        obj = Obj(self.tool, pts, **d)
        self.objs.append(obj)
        self.selected = obj
        self.drag["mode"] = "create"
        self.touch()

    def constrain(self, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if self.tool in ("line", "arrow"):
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            r = math.hypot(dx, dy)
            return [a[0] + r * math.cos(ang), a[1] + r * math.sin(ang)]
        s = max(abs(dx), abs(dy))
        return [a[0] + math.copysign(s, dx or 1), a[1] + math.copysign(s, dy or 1)]

    def on_drag_update(self, gesture, dx, dy):
        if not self.drag:
            return
        sx, sy = gesture.get_start_point()[1:]
        p = self.to_img(sx + dx, sy + dy)
        mode = self.drag.get("mode")
        last = self.drag["last"]
        self.drag["last"] = p
        if math.dist(p, self.drag["start"]) * self.zoom > 2:
            self.drag["moved"] = True
        obj = self.selected
        if mode == "create":
            if obj.kind == "freehand":
                obj.pts.append(p)
            else:
                obj.pts[1] = self.constrain(obj.pts[0], p) if self.drag["shift"] else p
            self.touch()
        elif mode == "move" and obj:
            obj.move(p[0] - last[0], p[1] - last[1])
            self.touch()
        elif mode == "resize" and obj:
            i = self.drag["handle"]
            if obj.kind in ("line", "arrow"):
                obj.pts[i] = p
            else:
                a, b = obj.pts
                if i in (0, 3):
                    a[0] = p[0]
                else:
                    b[0] = p[0]
                if i in (0, 1):
                    a[1] = p[1]
                else:
                    b[1] = p[1]
            self.touch()
        elif mode == "crop":
            self.crop[1] = [max(0, min(self.base.get_width(), p[0])), max(0, min(self.base.get_height(), p[1]))]
            self.crop[0] = [max(0, min(self.base.get_width(), self.crop[0][0])),
                            max(0, min(self.base.get_height(), self.crop[0][1]))]
            self.canvas.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        d, self.drag = self.drag, None
        if not d:
            return
        mode = d.get("mode")
        if mode == "create" and not d["moved"] and self.selected.kind != "freehand":
            # nur geklickt, nichts gezogen: kein leeres Objekt hinterlassen
            self.objs.remove(self.selected)
            self.selected = None
            self.undo_stack.pop()
            self.touch()
        elif mode in ("move", "resize") and not d["moved"] and self.tool == "select":
            self.undo_stack.pop()
        elif mode == "crop" and not d["moved"]:
            self.crop = None
        self.sync_props()
        self.canvas.queue_draw()

    # ---------- Text ----------

    def start_text(self, obj, new=False):
        if self.editing is not obj:
            self.finish_text()
            if not new:
                self.push_undo()
        self.editing = obj
        self.selected = obj
        self.im.focus_in()
        self.sync_props()
        self.canvas.queue_draw()

    def finish_text(self):
        obj, self.editing = self.editing, None
        if not obj:
            return
        self.im.focus_out()
        self.im.reset()
        if not obj.text.strip() and obj in self.objs:
            self.objs.remove(obj)
            if self.selected is obj:
                self.selected = None
        self.touch()

    def on_im_commit(self, im, text):
        if self.editing:
            self.editing.text += text
            self.touch()

    # ---------- Tastatur ----------

    def on_key(self, ctrl, keyval, keycode, state):
        name = Gdk.keyval_name(keyval) or ""
        ctrl_down = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if isinstance(self.get_focus(), Gtk.Editable) and not (ctrl_down and name.lower() in ("s", "w", "q")):
            return False  # Eingabe in der Leiste (z. B. Linienbreite) nicht abfangen

        if ctrl_down:
            lname = name.lower()
            actions = {
                "z": self.undo, "y": self.redo, "c": self.copy_clipboard,
                "s": self.save_as if shift else self.save, "w": self.close, "q": self.close,
                "plus": lambda: self.set_zoom(self.zoom * 1.25), "equal": lambda: self.set_zoom(self.zoom * 1.25),
                "kp_add": lambda: self.set_zoom(self.zoom * 1.25),
                "minus": lambda: self.set_zoom(self.zoom / 1.25), "kp_subtract": lambda: self.set_zoom(self.zoom / 1.25),
                "0": lambda: self.set_zoom(1 / self.get_scale_factor()), "kp_0": lambda: self.set_zoom(1 / self.get_scale_factor()),
            }
            if lname == "z" and shift:
                self.redo()
                return True
            if lname in actions:
                if lname in ("z", "y"):
                    self.finish_text()
                actions[lname]()
                return True
            return False

        if self.editing:
            if name == "Escape":
                self.finish_text()
            elif name == "BackSpace":
                self.editing.text = self.editing.text[:-1]
                self.touch()
            elif name in ("Return", "KP_Enter"):
                self.editing.text += "\n"
                self.touch()
            else:
                return self.im.filter_keypress(ctrl.get_current_event())
            return True

        if name == "Escape":
            if self.crop:
                self.crop = None
                self.canvas.queue_draw()
            elif self.selected:
                self.selected = None
                self.sync_props()
                self.canvas.queue_draw()
            else:
                self.set_tool("select")
            return True
        if name in ("Return", "KP_Enter") and self.crop:
            self.apply_crop()
            return True
        if name in ("Delete", "BackSpace") and self.selected:
            self.push_undo()
            self.objs.remove(self.selected)
            self.selected = None
            self.sync_props()
            self.touch()
            return True
        if name in ("Left", "Right", "Up", "Down") and self.selected:
            step = 10 if shift else 1
            dx = {"Left": -step, "Right": step}.get(name, 0)
            dy = {"Up": -step, "Down": step}.get(name, 0)
            self.push_undo()
            self.selected.move(dx, dy)
            self.touch()
            return True
        for key, _, accel in TOOLS:
            if name.lower() == accel.lower() and accel != "Escape":
                self.set_tool(key)
                return True
        return False

    # ---------- Bearbeiten ----------

    def push_undo(self):
        self.undo_stack.append((self.base, copy.deepcopy(self.objs)))
        self.redo_stack.clear()

    def restore(self, state):
        self.base, self.objs = state
        self.selected = None
        self.editing = None
        self.set_zoom(self.zoom)
        self.sync_props()
        self.touch()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append((self.base, copy.deepcopy(self.objs)))
            self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append((self.base, copy.deepcopy(self.objs)))
            self.restore(self.redo_stack.pop())

    def apply_crop(self):
        x1, y1, x2, y2 = (int(round(v)) for v in self.norm(self.crop))
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        self.push_undo()
        new = cairo.ImageSurface(cairo.FORMAT_ARGB32, x2 - x1, y2 - y1)
        cr = cairo.Context(new)
        cr.set_source_surface(self.base, -x1, -y1)
        cr.paint()
        self.base = new
        for obj in self.objs:
            obj.move(-x1, -y1)
        self.crop = None
        self.set_zoom(self.zoom)
        self.set_tool("select")
        self.touch()

    # ---------- Ausgabe ----------

    def png_bytes(self):
        self.finish_text()
        buf = io.BytesIO()
        self.composite().write_to_png(buf)
        return buf.getvalue()

    def copy_clipboard(self):
        # wl-copy bleibt im Hintergrund, die Zwischenablage überlebt also das Schließen des Editors
        p = subprocess.Popen(["wl-copy", "--type", "image/png"], stdin=subprocess.PIPE)
        p.communicate(self.png_bytes())
        self.toast("In die Zwischenablage kopiert")

    def write(self, path):
        with open(path, "wb") as f:
            f.write(self.png_bytes())
        self.save_path = path
        self.changed = False
        self.update_status()
        self.toast(f"Gespeichert: {path.replace(os.path.expanduser('~'), '~', 1)}")

    def save(self):
        if not self.save_path:
            folder = target_dir()
            os.makedirs(folder, exist_ok=True)
            self.save_path = os.path.join(folder, f"screenshot-{time.strftime('%Y-%m-%d_%H-%M-%S')}.png")
        self.write(self.save_path)

    def save_as(self):
        dialog = Gtk.FileDialog(title="Speichern unter")
        start = self.save_path or os.path.join(target_dir(), f"screenshot-{time.strftime('%Y-%m-%d_%H-%M-%S')}.png")
        dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(start)))
        dialog.set_initial_name(os.path.basename(start))

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if not path.lower().endswith(".png"):
                path += ".png"
            self.write(path)
        dialog.save(self, None, done)

    def on_close_request(self, *_):
        self.finish_text()
        if not self.changed:
            return False
        alert = Adw.AlertDialog(heading="Änderungen speichern?",
                                body="Das Bild wurde seit dem letzten Speichern verändert.")
        alert.add_response("cancel", "Abbrechen")
        alert.add_response("discard", "Verwerfen")
        alert.add_response("save", "Speichern")
        alert.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("save")
        alert.set_close_response("cancel")

        def done(dlg, res):
            choice = dlg.choose_finish(res)
            if choice == "discard":
                self.changed = False
                self.close()
            elif choice == "save":
                self.save()
                self.close()
        alert.choose(self, None, done)
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.mkm.snapmark", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name("org.mkm.snapmark")
        win = Editor(self, SOURCE)
        if SAVE_PATH is None:
            # Aufnahme liegt nur im Laufzeitordner; geladen ist sie, also wegräumen
            os.remove(SOURCE)
            os.rmdir(os.path.dirname(SOURCE))
            win.changed = True
        win.present()


if __name__ == "__main__":
    sys.exit(App().run([sys.argv[0]]))
