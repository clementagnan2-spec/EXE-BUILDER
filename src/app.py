"""ZipToExe - interface graphique."""
import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from compiler import BuildError, Options, compile_zip
from toolchain import setup_env, tools_dir

APP_TITLE = "ZipToExe – Compilateur de projets ZIP en .exe"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("780x580")
        self.minsize(660, 480)
        self.q: "queue.Queue" = queue.Queue()
        self.busy = False

        self.zip_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.entry_var = tk.StringVar()
        self.icon_var = tk.StringVar()
        self.win_var = tk.BooleanVar(value=False)

        self._build_ui()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill="x", padx=10, pady=10)
        frm.columnconfigure(1, weight=1)

        rows = [
            ("Projet (.zip) :", self.zip_var, self._pick_zip),
            ("Dossier de sortie :", self.out_var, self._pick_out),
            ("Icône .ico (Python, optionnel) :", self.icon_var, self._pick_icon),
        ]
        r = 0
        for label, var, cmd in rows:
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", **pad)
            ttk.Entry(frm, textvariable=var).grid(row=r, column=1, sticky="ew", **pad)
            ttk.Button(frm, text="Parcourir…", command=cmd).grid(row=r, column=2, **pad)
            r += 1

        ttk.Label(frm, text="Nom du .exe (optionnel) :").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.name_var).grid(row=r, column=1, sticky="ew", **pad)
        r += 1
        ttk.Label(frm, text="Fichier d'entrée (optionnel) :").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.entry_var).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="ex : src/main.py", foreground="gray").grid(row=r, column=2, **pad)
        r += 1
        ttk.Checkbutton(frm, text="Application sans fenêtre console (GUI)",
                        variable=self.win_var).grid(row=r, column=1, sticky="w", **pad)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10)
        self.btn = ttk.Button(bar, text="⚙  Compiler en .exe", command=self._start)
        self.btn.pack(side="left")
        self.open_btn = ttk.Button(bar, text="Ouvrir le dossier de sortie",
                                   command=self._open_out, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        self.prog = ttk.Progressbar(bar, mode="indeterminate")
        self.prog.pack(side="left", fill="x", expand=True, padx=8)

        self.log = scrolledtext.ScrolledText(self, height=18, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=10)
        self._log("Langages pris en charge : Python, Node.js, Go, Rust, .NET, CMake, C/C++.")
        found = setup_env()
        if found:
            self._log("Outils intégrés : " + ", ".join(found) + "\n")
        else:
            self._log(f"Aucun outil intégré (dossier introuvable : {tools_dir()}).")
            self._log("Les outils installés sur ce PC seront utilisés s'ils existent.\n")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- pickers
    def _pick_zip(self) -> None:
        p = filedialog.askopenfilename(title="Choisir le projet",
                                       filetypes=[("Archive ZIP", "*.zip")])
        if p:
            self.zip_var.set(p)
            if not self.out_var.get():
                self.out_var.set(str(Path(p).parent / "exe_output"))

    def _pick_out(self) -> None:
        p = filedialog.askdirectory(title="Dossier de sortie")
        if p:
            self.out_var.set(p)

    def _pick_icon(self) -> None:
        p = filedialog.askopenfilename(title="Icône", filetypes=[("Icône", "*.ico")])
        if p:
            self.icon_var.set(p)

    def _open_out(self) -> None:
        out = self.out_var.get()
        if out and os.path.isdir(out):
            os.startfile(out) if os.name == "nt" else None  # noqa: S606

    # ------------------------------------------------------------- build
    def _start(self) -> None:
        if self.busy:
            return
        zp, out = self.zip_var.get().strip(), self.out_var.get().strip()
        if not zp or not out:
            messagebox.showwarning(APP_TITLE, "Choisissez un ZIP et un dossier de sortie.")
            return
        opts = Options(name=self.name_var.get().strip() or "app",
                       entry=self.entry_var.get().strip(),
                       icon=self.icon_var.get().strip(),
                       windowed=self.win_var.get())
        self.busy = True
        self.btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.prog.start(12)
        self._log("─" * 60)
        threading.Thread(target=self._worker, args=(zp, out, opts), daemon=True).start()

    def _worker(self, zp: str, out: str, opts: Options) -> None:
        work = Path(tempfile.mkdtemp(prefix="ziptoexe_"))
        try:
            files = compile_zip(zp, out, opts, work, lambda m: self.q.put(("log", m)))
            self.q.put(("done", (True, [str(f) for f in files])))
        except BuildError as e:
            self.q.put(("done", (False, str(e))))
        except Exception as e:  # noqa: BLE001
            self.q.put(("done", (False, f"Erreur inattendue : {e}")))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _poll(self) -> None:
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self._log(val)
                else:
                    self._finish(*val)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self, ok: bool, info) -> None:
        self.busy = False
        self.prog.stop()
        self.btn.configure(state="normal")
        if ok:
            self.open_btn.configure(state="normal")
            self._log("\n✅ Terminé !")
            messagebox.showinfo(APP_TITLE, "Compilation réussie :\n\n" + "\n".join(info))
        else:
            self._log(f"\n❌ {info}")
            messagebox.showerror(APP_TITLE, info)


if __name__ == "__main__":
    App().mainloop()
