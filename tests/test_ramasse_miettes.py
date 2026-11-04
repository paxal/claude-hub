"""Objets lâchés dans la boucle GTK, jamais dans un thread de fond : ramasse-miettes
lancé depuis la boucle (collect_in_main_loop), fonctions de in_thread et start_thread
lâchées dans la boucle. Un widget détruit dans un thread plantait claude-hub (SIGSEGV
dans la validation d'un MdView pendant son dispose)."""
import gc
import sys
import threading
import time
import unittest
import warnings
import weakref
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import gtk  # noqa: E402
from claude_hub.gtk import GLib  # noqa: E402


class Probe:
    """Objet qui note dans quel thread il est libéré."""
    def __init__(self, freed):
        weakref.finalize(self, lambda: freed.append(threading.current_thread() is threading.main_thread()))

    def work(self):
        return 42

    def fail(self):
        raise ValueError("raté")

    def done(self, res):
        self.got = res

    def run(self):
        time.sleep(0.1)  # le temps que la boucle lâche ses propres références


def iterate():
    with warnings.catch_warnings():  # émis à chaque appel par PyGObject sous Python 3.14
        warnings.simplefilter("ignore", DeprecationWarning)
        GLib.MainContext.default().iteration(False)


def iterate_until(cond, timeout=3):
    """Fait tourner la boucle GTK jusqu'à cond() (False au bout de timeout secondes)."""
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        iterate()
        time.sleep(0.005)
    return True


real_idle_add = GLib.idle_add


def late_idle_add(*args, **kw):
    """idle_add qui laisse la boucle traiter la fonction avant que le thread qui l'a
    programmée ait fini : le thread lâche alors ses références en dernier."""
    source = real_idle_add(*args, **kw)
    if threading.current_thread() is not threading.main_thread():
        time.sleep(0.2)
    return source


class ThreadsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(GLib, "idle_add", late_idle_add)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.threads = threading.active_count()

    def wait_threads(self):
        self.assertTrue(iterate_until(lambda: threading.active_count() <= self.threads))

    def test_in_thread_lache_tout_dans_la_boucle(self):
        freed = []

        def start():
            p, q = Probe(freed), Probe(freed)
            gtk.in_thread(p.work, q.done)
        start()
        self.assertTrue(iterate_until(lambda: len(freed) == 2))
        self.wait_threads()
        self.assertEqual(freed, [True, True])

    def test_in_thread_exception_lachee_dans_la_boucle(self):
        freed, errors = [], []

        def start():
            p = Probe(freed)
            # sans garder l'exception, dont les frames retiennent la sonde
            gtk.in_thread(p.fail, lambda res: errors.append("ok"), lambda e: errors.append(type(e)))
        start()
        self.assertTrue(iterate_until(lambda: freed))
        self.wait_threads()
        self.assertEqual(errors, [ValueError])
        self.assertEqual(freed, [True])

    def test_in_thread_pas_de_repetition(self):
        calls = []
        gtk.in_thread(lambda: 1, lambda res: calls.append(res) or True)
        self.assertTrue(iterate_until(lambda: calls))
        self.wait_threads()
        for _ in range(20):
            iterate()
        self.assertEqual(calls, [1])

    def test_start_thread_lache_la_cible_dans_la_boucle(self):
        freed = []

        def start():
            return gtk.start_thread(Probe(freed).run)
        thread = start()
        self.assertTrue(iterate_until(lambda: freed))
        thread.join()
        self.assertEqual(freed, [True])

    def test_start_thread_exception(self):
        freed = []

        def start():
            return gtk.start_thread(Probe(freed).fail)
        with mock.patch("traceback.print_exc") as printed:
            thread = start()
            self.assertTrue(iterate_until(lambda: freed))
        thread.join()
        printed.assert_called_once()
        self.assertEqual(freed, [True])


class CollecteTest(unittest.TestCase):
    def test_collecte_coupee_et_relancee_par_la_boucle(self):
        self.addCleanup(gc.enable)
        with mock.patch.object(GLib, "timeout_add_seconds") as timeout:
            gtk.collect_in_main_loop()
        self.assertFalse(gc.isenabled())
        timeout.assert_called_once_with(1, gtk.collect_if_due)

    def test_seuils(self):
        cases = [((1999, 9, 9), None), ((100, 10, 10), None), ((2000, 3, 0), 0),
                 ((2500, 10, 3), 1), ((2000, 10, 10), 2), ((2000, 9, 10), 2)]
        for counts, gen in cases:
            with mock.patch("gc.get_count", return_value=counts), \
                 mock.patch("gc.get_threshold", return_value=(2000, 10, 10)), \
                 mock.patch("gc.collect") as collect:
                self.assertTrue(gtk.collect_if_due())
            if gen is None:
                collect.assert_not_called()
            else:
                collect.assert_called_once_with(gen)

    def abandon_cycle_then_allocate_in_thread(self):
        """Cycle abandonné par la boucle, puis un thread qui alloue de quoi dépasser le
        seuil de la génération 0 ; renvoie (libérations, allocations gardées)."""
        gc.collect()
        freed, junk = [], []

        def abandon():
            p = Probe(freed)
            p.cycle = p
        abandon()
        thread = threading.Thread(target=lambda: junk.extend([] for _ in range(3 * gc.get_threshold()[0])))
        thread.start()
        thread.join()
        return freed, junk

    def test_sans_correction_le_thread_libere_le_cycle(self):
        """La cause du plantage : la collecte part dans le thread qui alloue."""
        freed, _junk = self.abandon_cycle_then_allocate_in_thread()
        self.assertEqual(freed, [False])

    def test_cycle_libere_dans_la_boucle_pas_dans_le_thread(self):
        self.addCleanup(gc.enable)
        with mock.patch.object(GLib, "timeout_add_seconds"):
            gtk.collect_in_main_loop()
        freed, _junk = self.abandon_cycle_then_allocate_in_thread()
        self.assertEqual(freed, [])
        self.assertTrue(gtk.collect_if_due())
        self.assertEqual(freed, [True])


if __name__ == "__main__":
    unittest.main()
