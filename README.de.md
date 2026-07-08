# aiflow

**aiflow macht aus jedem Repository mit einem Befehl eine gesteuerte, KI-getriebene
Software-Lieferstrecke.** Es verbindet [Claude Code](https://docs.claude.com/en/docs/claude-code)
mit dauerhaftem Task-Tracking, autonomen Arbeits-Loops, einem Code-Wissensgraphen, spezialisierten
Review-/Audit-Agenten, Kostenkontrolle, erzwungener Code-Qualität und einem konfigurierbaren
Git-Branching-Modell — damit ein KI-Agent ein Issue annehmen, planen, in einheitlichem Stil
implementieren, testen, gegen Akzeptanzkriterien reviewen, auf Security/Qualität auditieren und über
einen echten Release-Prozess ausliefern kann.

Es ist **herstellerneutral** (dein eigener Anthropic API Key *oder* Claude-Code-OAuth-Token — kein
Drittanbieter-Hub), läuft auf **Windows, Linux und macOS** und ist **projektbasiert**: Secrets und
Einstellungen liegen im Projekt, nie global.

> 🇬🇧 This guide is also available in **[English → README.md](README.md)**.

---

## Inhalt

1. [Was ist aiflow & warum](#1-was-ist-aiflow--warum)
2. [Neu bei KI-Coding? Hier starten](#2-neu-bei-ki-coding-hier-starten)
3. [Installation](#3-installation)
4. [Projekt einrichten](#4-projekt-einrichten)
5. [Befehlsreferenz](#5-befehlsreferenz)
6. [Agenten](#6-agenten)
7. [Slash-Command-Skills](#7-slash-command-skills)
8. [Die mitgelieferte Toolchain & warum jedes Teil](#8-die-mitgelieferte-toolchain--warum-jedes-teil)
9. [Der Liefer-Workflow](#9-der-liefer-workflow)
10. [Autonomes Arbeiten: der Ralph-Loop](#10-autonomes-arbeiten-der-ralph-loop)
11. [Audit-Agenten](#11-audit-agenten)
12. [Qualität & Erzwingung](#12-qualität--erzwingung)
13. [Token- & Kostenoptimierung](#13-token--kostenoptimierung)
14. [Modell-Routing](#14-modell-routing)
15. [Git-Branching-Governance](#15-git-branching-governance)
16. [Memory](#16-memory)
17. [Konfigurationsmodell](#17-konfigurationsmodell)
18. [Tools global, Konfiguration projektspezifisch](#18-tools-global-konfiguration-projektspezifisch)
19. [CI/CD](#19-cicd)
20. [Headless & Container](#20-headless--container)
21. [Anpassen](#21-anpassen)
22. [Projektstruktur](#22-projektstruktur)
23. [Aktualisieren](#23-aktualisieren)
24. [Troubleshooting](#24-troubleshooting)
25. [Mitwirken](#25-mitwirken)
26. [Feedback, Ideen & Bug-Meldungen](#26-feedback-ideen--bug-meldungen)
27. [Lizenz](#27-lizenz)

---

## 1. Was ist aiflow & warum

KI-Coding-Agenten sind mächtig, aber vergesslich und unstrukturiert: Sie starten jede Sitzung bei
null, weichen von deiner Architektur ab, schreiben uneinheitlich, überspringen Tests und führen kein
Protokoll über Entscheidungen. aiflow behebt das, indem es eine **vollständige, meinungsstarke
Arbeitsumgebung** für Claude Code in dein Repo installiert:

- **Dauerhafte Tasks** in Beads (git-/Dolt-gestützt) — Arbeit überlebt Sitzungen und Kontext-Resets.
- **Spezialisierte Agenten** für Planung, Implementierung, Review, Tests, Security, Qualität u. m.
- **Autonome Loops**, die ganze Aufgaben unbeaufsichtigt erledigen und ihren Status melden.
- **Ein Code-Wissensgraph** — der Agent antwortet aus der Struktur statt Dateien neu zu lesen.
- **Erzwungene Qualität**: Google Style, Auto-Format, Lint, Tests, Conventional Commits via git-Hooks.
- **Kostenkontrolle**: knapper Output, CLI-Output-Filter, günstiges Modell-Routing, Verbrauchsmessung.
- **Git-Governance**: konfigurierbares Branching-Modell mit PR-Regeln, Releases, Versionierung.
- **Ein echter Review-Trail**: Akzeptanzkriterien-Checks, Anforderungs-Audits, priorisierte Funde.

Alles sind Dateien im Repo (`CLAUDE.md`, `.claude/`, `.aiflow/`, `.githooks/`) — transparent,
editierbar, kein Lock-in.

---

## 2. Neu bei KI-Coding? Hier starten

Eine Erklärung in einfachen Worten. Überspringen, wenn du Claude Code kennst.

- **KI / LLM:** Software, die Text vorhersagt und aus Anweisungen + Kontext Code schreibt/ändert. Sie
  hat **kein Gedächtnis zwischen Sitzungen** — du musst jedes Mal Kontext liefern. Genau dieses
  „Kontextproblem" löst aiflow zum Großteil.
- **Claude Code:** Anthropics Terminal-/IDE-Agent, der — mit Erlaubnis — Dateien liest, Befehle
  ausführt, Code ändert und Tools nutzt. aiflow konfiguriert ihn für dein Projekt.
- **API Key vs. OAuth-Token:** Anmeldung per **Anthropic API Key** (pro Nutzung) oder
  **Claude-Code-OAuth-Token** (`claude setup-token`, nutzt dein Abo). Beides unterstützt; in `.env`
  (nie committet).
- **Agent:** ein fokussierter KI-Arbeiter mit Rolle + System-Prompt (z. B. *reviewer*, *implementer*).
- **Skill / Slash-Command:** wiederverwendbare Anweisung, ausgelöst mit `/name` (z. B. `/implement`).
- **Hook:** Script, das die Umgebung automatisch bei Ereignissen ausführt (nach Edit, bei
  Sitzungsstart, vor Push). aiflow nutzt Hooks für Auto-Format, Erzwingung, Output-Stil.
- **Memory:** dauerhafte Fakten in Dateien (`.claude/memory/`) plus Beads-Task-Store und
  graphify-Graph — jede Sitzung startet informiert.
- **MCP (Model Context Protocol):** Standard, um externe Tools anzubinden (GitHub-Issues,
  Dateisystem, Code-Graph). aiflow erzeugt die MCP-Konfiguration.
- **Claudes Projekteinstellungen:** einfache Dateien steuern das Verhalten — `CLAUDE.md` (Regeln für
  alle Agenten), `.claude/settings.json` (Berechtigungen + Hooks), `docs/architecture/` (arc42 + ADRs).

Ziel: Auch ein Laie führt `aiflow init` aus, beantwortet ein paar Fragen und erhält ein Setup, das die
KI standardmäßig zu **qualitativ gutem, günstigem, reviewbarem** Output lenkt.

---

## 3. Installation

**Voraussetzungen (selbst):** [Node.js](https://nodejs.org) (für `npm`) und **Git Bash** (unter
Windows — die Kernlogik ist Bash, die PowerShell-Hülle delegiert dorthin). Den Rest installiert aiflow.

**Windows (PowerShell):**
```powershell
cd C:\dev\aiflow
powershell -ExecutionPolicy Bypass -File .\install.ps1   # fügt aiflow dem PATH hinzu
# neues Terminal, dann:
aiflow doctor
```
**Linux / macOS / Git-Bash:**
```bash
cd /pfad/zu/aiflow
bash install.sh
aiflow doctor
```

`aiflow doctor` zeigt Vorhandenes/Fehlendes. Toolchain installieren:
```bash
aiflow install-deps --all
```
Installiert (user-global) claude, **beads + dolt** (Datenbank-Backend), jq, die passende VCS-CLI
(`gh`/`glab`) und — falls im Projekt aktiviert — task-master, claude-code-router, rtk,
graphify + uv. Unter Windows bevorzugt **winget** (dann scoop), macOS Homebrew, Linux
System-Paketmanager / offizielle Skripte. **Eine Container-Engine wird nie automatisch installiert**
(**Podman oder Docker** bei Bedarf selbst, für GitHub-MCP oder Headless-Container-Runs). Oder einfach
`aiflow init` im Projekt — es bietet an, genau die aktivierten Tools zu installieren.

---

## 4. Projekt einrichten

```bash
cd /pfad/zu/deinem/projekt
aiflow init            # interaktiv — stellt Fragen, verdrahtet alles
aiflow init . --yes    # alle Defaults (nicht-interaktiv / CI)
```

`aiflow init` fragt und schreibt die Antworten nach **`.aiflow/config.json`**:

1. **caveman** knapper Output? + Modus (`full` empfohlen / `lite` / `ultra`).
2. **rtk** CLI-Output-Filter?
3. **claude-code-router** für günstige/lokale Modelle bei einfachen Tasks?
4. **graphify** Code-Wissensgraph (Memory-Optimierung)?
5. **claude-task-master** Task-Zerlegung?
6. **filesystem-MCP**?
7. **VCS-Host** — github / gitlab / bitbucket.
8. **Projektziel** — was es erreichen soll (→ Memory).
9. **Zielarchitektur** — hexagonal / Schichten / MVC / … (→ Memory).
10. **OS & IDE** (VS Code / IntelliJ / andere) — damit die KI die richtigen Befehle wählt.
11. **claude-code-templates** durchsuchen?
12. **Git-Branching-Modell** — simple / gitflow / none, dann strikte Regeln, PR-only, Auto-Release,
    Versionsstrategie, Release-Tags, chore-Branches (siehe §15).
13. Angebot, **fehlende Tools zu installieren**.

Außerdem: `.env` (aus `.env.example`), `git init`, `bd init` und Rendern aus der Config. Jede Antwort
später ändern: **`aiflow change-settings`** (fragt neu, wendet neu an).

### Neues vs. bestehendes (Brownfield) Projekt

`aiflow init` erkennt den Fall (bestehend = Ordner hat bereits Git-Historie oder Quelldateien) und
passt sich an:

| | **Neues Projekt** (leerer Ordner) | **Bestehendes Projekt** (hat Code / Git-Historie) |
|---|---|---|
| Deine Dateien | nichts zu schützen | **bleiben erhalten** — Templates werden *no-clobber* kopiert; vorhandene `CLAUDE.md`, `.gitignore` usw. werden nie überschrieben (`--force` zum Ersetzen) |
| `git init` | läuft | übersprungen (Historie bleibt) |
| `bd init` | läuft | nur wenn `.beads/` fehlt; Git-Hooks werden mit Beads-Hooks gemerged |
| Branching-Modell | permanente Branches aus dem ersten Commit | `main`/`develop` aus aktuellem `HEAD` nur falls fehlend; dein aktueller Branch bleibt unangetastet |
| Architektur-Wissen | du füllst `CLAUDE.md §1/§2` + arc42 + `project-aim` | aiflow **bietet `aiflow onboard` an**: studiert den Code, schreibt `.claude/memory/codebase-map.md` + `conventions.md`, füllt die `[EDIT ME]`-Blöcke in `CLAUDE.md` und `docs/architecture/arc42.md` |
| Empfohlener Folgeschritt | losbauen | Baseline-Audits (`aiflow security-check`, `quality-check`, `dependency-check`, `test-gap`, `docs-check`) zum Befüllen des Backlogs |

**Empfohlener Ablauf für ein bestehendes Projekt:**
```bash
cd /pfad/zum/bestehenden/repo
aiflow init               # bewahrt deine Dateien; beim Onboarding "ja" sagen
# (oder später explizit:)  aiflow onboard
# Gelerntes prüfen & abgleichen:
#   .claude/memory/codebase-map.md, CLAUDE.md §1/§2, docs/architecture/arc42.md
aiflow index              # graphify-Graph über den Bestandscode bauen
aiflow security-check     # optional: Backlog mit priorisierten Funden befüllen
aiflow shell
```
init bleibt idempotent bei erneutem Lauf auf einem konfigurierten Projekt; `--force` nur, wenn du
aiflows eigene Templates bewusst auf Defaults zurücksetzen willst (dein Anwendungscode wird nie
angefasst).

Dann:
```bash
# .env füllen: GITHUB_TOKEN (oder GITLAB_/BITBUCKET_) + ANTHROPIC_API_KEY oder CLAUDE_CODE_OAUTH_TOKEN
aiflow shell            # lädt .env und startet Claude Code
```

---

## 5. Befehlsreferenz

| Befehl | Zweck |
|--------|-------|
| `aiflow init [--yes] [--force] [--no-git] [--no-beads]` | Projekt einrichten (interaktiv). |
| `aiflow change-settings [--no-token-saving]` | Projekt-Config neu einstellen + neu anwenden (Alias `settings`); `--no-token-saving` = caveman + rtk aus. |
| `aiflow install-deps [--all]` | Fehlende Tools installieren (aktivierte; `--all` = alle). |
| `aiflow doctor` | Voraussetzungen + gesetzte Tokens prüfen. |
| `aiflow shell [--router]` | `.env` laden, Claude Code starten (`--router` = via claude-code-router). |
| `aiflow ralph "<task>"` | Headless Ralph-Loop bis COMPLETE/BLOCKED. |
| `aiflow security-check` | Security-Audit des ganzen Projekts → `[security-advisor]` Beads. |
| `aiflow quality-check` | Refactoring-/Qualitäts-Audit → `[technical issue]` Beads. |
| `aiflow requirements-check` | Beratendes Issue-Qualitäts-Audit vs. Architektur (nur Report). |
| `aiflow dependency-check` | Dependency-Audit (Vulns/veraltet/ungenutzt/Lizenz) → `[dependency]` Beads. |
| `aiflow test-gap` | Ungetestete kritische Pfade → `[test gap]` Beads. |
| `aiflow perf-check` | Performance-Audit → `[performance]` Beads. |
| `aiflow docs-check` | Doku/Code-Drift → `[docs]` Beads. |
| `aiflow a11y-check` | Strikte WCAG-2.2-AA-Barrierefreiheitsprüfung → `[accessibility]` Beads. |
| `aiflow modernize-check` | Brownfield-Modernisierungskonzepte → Bericht `.aiflow/modernization-report.md` für den Architekten. |
| `aiflow onboard` | Bestandscode in Memory + CLAUDE.md + arc42 lernen. |
| `aiflow release [--push]` | Release gemäß Branching-Modell (Version-Bump + Tag). |
| `aiflow protect` | Server-seitigen Branch-Schutz setzen (GitHub). |
| `aiflow index` | graphify Code-Wissensgraph bauen/aktualisieren. |
| `aiflow cost [...]` | Token-/Kosten-Baseline via ccusage. |
| `aiflow upgrade` | Mitgelieferte Toolchain (beads, rtk, graphify, …) aktualisieren. |
| `aiflow version` | Version ausgeben. |

In Claude Code zusätzlich die Slash-Command-Skills aus §7.

---

## 6. Agenten

Spezial-Subagenten in `.claude/agents/`. Claude wählt nach `description` automatisch, oder du rufst
explizit auf. Drei Gruppen:

**Liefer-Agenten** (machen die Arbeit):
- **architect** — Systemdesign; ADRs + arc42-Updates + Task-Aufteilung. Kein Feature-Code.
- **planner** — Ziel/Epic/Issue → kleine Beads-Tasks mit testbaren Akzeptanzkriterien + echten
  Abhängigkeiten.
- **implementer** — Senior Engineer für genau einen freien Bead: Voranalyse (Architektur-Fit,
  Aufwand, Komplexität) vor dem Code, zielgenaues Refactoring wenn nötig, SOLID/DRY/KISS/YAGNI,
  testbar by design (DI, deterministisch), bewährte Frameworks/Patterns statt Eigenbau,
  PO-verständliche Rückfragen mit festgehaltenen Entscheidungen, Quality Gates (statische Analyse,
  >80 % Coverage, BDD-E2E, Logging, `.http`-Dateien, Metrik-Ziele); stoppt als BLOCKED bei
  unklaren Kriterien.
- **reviewer** — Architekt **und** Quality Gate in einem: Architektur-/Design-/Risiko-Review
  (Layer, Modulgrenzen, SOLID, technische Schulden, Over-/Underengineering, Schwachstellen,
  Concurrency, Breaking Changes) plus die objektive §3a-Checkliste; Suggestions werden als
  `[suggestion]`-Beads für die nächste Loop festgehalten; Verdikt PASS / CHANGES REQUIRED.
- **tester** — Test-/QA-Engineer: Negativ-/Edge-/Boundary-/Exception-/Invalid-Input-Tests plus
  Testqualitäts-Audit (Assertions, Determinismus, Unabhängigkeit); läuft, wenn die Voranalyse
  hohes Risiko/Komplexität meldet; meldet Bugs statt Tests aufzuweichen.

**Audit-Agenten** (manuell via aiflow, read-only auf Code, legen priorisierte Beads an — siehe §11):
- **security-advisor** → `[security-advisor]`
- **quality-check** → `[technical issue]`
- **dependency-auditor** → `[dependency]`
- **test-gap-advisor** → `[test gap]`
- **performance-advisor** → `[performance]`
- **docs-sync** → `[docs]`
- **accessibility-checker** → `[accessibility]` (strikt WCAG 2.2 AA; empfiehlt ein automatisiertes A11y-Tool für die E2E-Suite; `aiflow a11y-check`)
- **modernization-advisor** → nur Bericht: Modernisierungskonzepte (Microservices, REST/Cloud-Native, git statt svn, supportete Stacks, fehlende Unit-/BDD-/E2E-Frameworks) nach `.aiflow/modernization-report.md` für den Architekten (`aiflow modernize-check`)
- **requirements-check** — beratend; benotet Issue-Qualität vs. Architektur, **nur Report** (keine
  Beads, keine Änderungen).

**Brownfield-Agent:**
- **onboarder** — studiert Bestandscode und persistiert das Gelernte in `.claude/memory/`,
  `CLAUDE.md` und arc42; **schlägt aus dem Verständnis ein Projektziel (Aim) vor** und fragt dich,
  ob es stimmt. Schreibt nur Doku/Memory.

Die mitgelieferten Agenten sind **bewusst generisch** — eine starke, universelle Basis, nicht das
Ziel: **passe sie an die Bedürfnisse deines Projekts an** (Markdown editieren: Prompt, `tools:`,
`model:` — z. B. Fachsprache, Review-Fokus, Test-Stack). Siehe §21.

---

## 7. Slash-Command-Skills

In Claude Code auslösbar (`.claude/commands/`):

- **Lieferung:** `/intake-issue <n>` (GitHub/GitLab/Bitbucket-Issue → Beads), `/decompose <ziel|prd>`
  (claude-task-master → Beads), `/plan-epic`, `/implement [bead] [ralph|no-ralph]`, `/review-ac`, `/arch "<frage>"`.
- **Audits:** `/security-check`, `/quality-check`, `/requirements-check`, `/dependency-check`,
  `/test-gap`, `/perf-check`, `/docs-check`, `/a11y-check`, `/modernize-check`.
- **Brownfield / Orientierung:** `/onboard`, `/explain <pfad>`, `/standup`.

(Beads und der Ralph-Loop sind zudem als eigene Plugin-Skills verfügbar, z. B. `/beads:ready`.)

---

## 8. Die mitgelieferte Toolchain & warum jedes Teil

Jedes Tool verdient seinen Platz durch mehr **Qualität**, weniger **Token-Kosten** oder
**autonome, prüfbare** Lieferung.

- **Claude Code** — die Agent-Laufzeit, auf der alles aufbaut.
  https://docs.claude.com/en/docs/claude-code · *plant, editiert, führt Tools aus — nicht nur Chat.*
- **Beads (`bd`)** — git-/Dolt-gestützter Issue-Tracker. https://github.com/steveyegge/beads ·
  *dauerhafter Task-Speicher mit Abhängigkeiten; Arbeit überlebt Sitzungs-/Kontext-Resets.*
- **Dolt** — versionierte SQL-DB hinter Beads. https://github.com/dolthub/dolt · *branch/merge/diff-
  Historie für Tasks — echter Audit-Trail.*
- **Ralph-Loop** — autonome „iteriere bis fertig"-Schleife. *erledigt eine Aufgabe unbeaufsichtigt,
  stoppt bei COMPLETE/BLOCKED, schreibt `result.json`.*
- **claude-task-master** — Ziel/PRD → Task-Baum mit Abhängigkeiten.
  https://github.com/eyaltoledano/claude-task-master · *gute Zerlegung = besserer, reviewbarer
  Output; `claude-code`-Provider, kein Extra-Key.*
- **graphify** — abfragbarer Wissensgraph des Codes (Imports, Call-Graphs) über MCP.
  https://github.com/safishamsi/graphify · *Struktur abfragen statt Dutzende Dateien neu zu lesen —
  viel weniger Tokens, weniger DRY-Verstöße.*
- **ccusage** — Token-/Kosten-Auswertung. https://github.com/ryoppippi/ccusage · *erst messen, dann
  optimieren (`aiflow cost`).*
- **claude-code-router** — Anfragen auf verschiedene Modelle routen (Anthropic, DeepSeek, lokales
  Ollama…). https://github.com/musistudio/claude-code-router · *günstig/lokal für Einfaches,
  Top-Modelle für schweres Denken — meist 50–99 % billiger.*
- **rtk** — filtert/komprimiert ausführliches Befehls-Output vor dem Kontext.
  https://www.rtk-ai.app/ · *behält Fehler/Diffs, kürzt Rauschen — oft 60–90 % weniger Tokens; pro
  Projekt aktiviert.*
- **caveman** — Modus für knappen Output. *~75 % weniger Output-Tokens; Code/Commits/Security normal.*
- **Podman / Docker** — containerisieren den Headless-Loop (`docker/run.sh`, Engine auto-erkannt). ·
  *reproduzierbar „läuft überall gleich".*
- **claude-code-templates** — Community-Marktplatz für Agenten/Commands/MCPs/Hooks.
  https://github.com/davila7/claude-code-templates · *erprobte Zusatz-Configs einbinden.*
- **Filesystem-MCP** — sicherer, strukturierter Dateizugriff.
  https://github.com/modelcontextprotocol/servers
- **GitHub / GitLab / Bitbucket** — Issue-Einlesen in Beads von allen drei.

---

## 9. Der Liefer-Workflow

```
Issue (GitHub / GitLab / Bitbucket)
  └─ /intake-issue ─▶ Beads-Tasks (mit Akzeptanzkriterien)
       └─ /decompose (claude-task-master) ─▶ Subtasks + Abhängigkeiten
            └─ /beads:ready ─▶ Task wählen
                 └─ /implement ─▶ Code + Tests, Google Style      (implementer)
                      └─ /review-ac ─▶ Gate gegen Akzeptanzkriterien (reviewer)
                           └─ Commit (Conventional Commits + Bead-ID) ─▶ PR ─▶ Release
```

Eine Aufgabe ist **DONE** nur wenn: AK erfüllt • Tests grün • Style/Lint sauber • Review-Gate
bestanden • Bead geschlossen • Commit referenziert Bead-ID (CLAUDE.md §10).

---

## 10. Autonomes Arbeiten: der Ralph-Loop

Für größere Aufgaben an den **Ralph-Loop** übergeben — der Agent iteriert bis `COMPLETE` oder `BLOCKED`.

- **Interaktiv:** `/ralph-loop` in Claude Code.
- **Headless:** `aiflow ralph "implement bd-12"` — jede Iteration schreibt `result.json`
  (`{status, summary, next, blocker}`); per `.env` getunt (`RALPH_MAX_ITERATIONS`,
  `RALPH_TIMEOUT_SECONDS`, `RALPH_PERMISSION_MODE`). Funktioniert mit env-Token **oder** deinem
  gespeicherten Claude-Login (OAuth).
- **In CI:** derselbe Loop via `.github/workflows/agent.yml` (manuell, `agent`-Label oder nächtlich, §19).
- **Containerisiert:** `docker/run.sh` — Container via **Podman oder Docker** (auto-erkannt;
  überschreiben mit `AIFLOW_CONTAINER=podman|docker`).

---

## 11. Audit-Agenten

Auf Abruf; jeder scannt das ganze Projekt read-only und legt priorisierte Beads mit erkennbarem
Prefix an, damit ein Mensch/PO sichten kann. Ändern nie Code.

| Befehl | Findet | Beads-Prefix |
|--------|--------|--------------|
| `aiflow security-check` | Injection, Secrets, Authz, Crypto, SSRF/XSS, Supply Chain | `[security-advisor]` |
| `aiflow quality-check` | toter/vereinfachbarer Code, Duplikate, Komplexität | `[technical issue]` |
| `aiflow dependency-check` | verwundbare/veraltete/ungenutzte Deps, Lizenzen | `[dependency]` |
| `aiflow test-gap` | ungetestete kritische / stark genutzte Pfade | `[test gap]` |
| `aiflow perf-check` | N+1, sync-IO, O(n²), fehlende Pagination/Indizes | `[performance]` |
| `aiflow docs-check` | README/CLAUDE/arc42/API-Drift | `[docs]` |
| `aiflow a11y-check` | WCAG 2.2 AA: Kontrast, Tastatur, ARIA, Labels (strikt) | `[accessibility]` |
| `aiflow modernize-check` | EOL-Stacks, Monolith→Microservices, Legacy→REST/Cloud-Native, svn→git, fehlende Test-Frameworks | *nur Bericht* |

Severity → Beads-Prio (Critical→P0 … Low→P3); Funde werden gegen offene Issues gleichen Prefixes
dedupliziert.

Separat ist **`aiflow requirements-check`** rein beratend: benotet die Beschreibungsqualität/
-vollständigkeit jedes Issues gegen die Architektur, flaggt unbeschriebene Fälle und schreibt
`.aiflow/requirements-report.md` — ändert nichts und entscheidet nicht über die Umsetzung.

---

## 12. Qualität & Erzwingung

Standardmäßig an:

- **Code-Stil:** Google Style für **jede** Sprache (CLAUDE.md §3), mit Formattern pro Sprache.
- **Auto-Format:** ein PostToolUse-Hook formatiert Dateien direkt nach KI-Edits.
- **pre-commit-Hook:** blockt den Commit, wenn Format + Lint + Unit-Tests nicht bestehen.
- **commit-msg-Hook:** lehnt Nicht-**Conventional-Commits** ab.
- **pre-push-Hook:** erzwingt das Branching-Modell (§15).
- **Review-Gate:** `/review-ac` + der *reviewer*-Agent — Architekt **und** Quality Gate in einem:
  Architektur-/Design-/Risiko-Review plus objektive Freigabe-Checkliste; Verdikt PASS oder CHANGES
  REQUIRED; Out-of-Scope-Ideen werden als `[suggestion]`-Beads für die nächste Loop festgehalten.
- **Quality Gates (CLAUDE.md §3a):** statische Analyse bei jeder Umsetzung (Tool oder der Agent
  selbst — keine Code Smells), >80 % Coverage der geänderten Logik + alle nicht-statischen
  Methoden getestet, Unit- + BDD-End-to-End-Tests immer, Logging mit Leveln Pflicht, plus
  objektive Metrik-Ziele (0 neue Duplikate/Smells, 0 Architekturverletzungen, 0 Linter-/
  Compiler-Warnings, 0 High/Critical-Security-Findings).
- **REST-`.http`-Dateien (CLAUDE.md §3b):** jeder neue/geänderte Endpunkt bekommt eine in der IDE
  testbare `.http`-Datei; Host/Port/Testzugang aus `.env` (`APP_HOST`, `APP_PORT`,
  `TEST_USERNAME`, `TEST_PASSWORD`).

Hooks in `.githooks/` (über `core.hooksPath`, mit Beads-Hooks gemerged). Notfall-Bypässe existieren
(`AIFLOW_SKIP_VERIFY=1`, `AIFLOW_SKIP_COMMIT_LINT=1`, `AIFLOW_ALLOW_DIRECT_PUSH=1`), sind aber unerwünscht.

---

## 13. Token- & Kostenoptimierung

Der gemessene Stack — **messen → routen → filtern → knapp sein → Kontext schlank halten**:

1. **Messen** — `aiflow cost` (ccusage) als Baseline, bevor du optimierst.
2. **Routen** — `aiflow shell --router` schickt Einfaches/Hintergrund auf günstige/lokale Modelle,
   schweres Denken auf Top-Modelle (§14).
3. **Filtern** — rtk komprimiert lautes Befehls-Output vor dem Kontext (Fehler/Diffs bleiben).
4. **Knapp** — caveman entfernt Füllwörter (Code/Commits/Security bleiben normal), Default `full`.
5. **Schlanker Kontext** — graphify: Code-Graph abfragen statt Dateien neu zu lesen.

---

## 14. Modell-Routing

`aiflow shell --router` startet Claude Code über **claude-code-router**. Es klassifiziert jede
Anfrage und ordnet ein von dir gewähltes Modell zu:

| Route | Wann | Empfohlenes Modell |
|-------|------|--------------------|
| `default` | normales interaktives Coding | stark (z. B. Sonnet) |
| `think` | schweres Denken / Planung | Top (z. B. Opus) |
| `background` | günstige/automatische Schritte, CI/CD-Routine | lokal Ollama / DeepSeek |
| `longContext` | sehr große Eingaben | Long-Context-Modell |
| `webSearch` | Websuche-Anfragen | web-fähiges Modell |

Config im Home (`~/.claude-code-router/config.json`, nie committet) — Vorlage
`.aiflow/router-config.example.json`. Sie hat `Providers` (mit API-Keys) und `Router`
(Route → `provider,model`). Mögliche Keys: Anthropic, DeepSeek, OpenRouter, Gemini; Ollama ist lokal,
braucht keinen. Ein eigener JS-Router (`CUSTOM_ROUTER_PATH`) erlaubt bedingte Regeln wie „CI → Ollama"
oder „auth/security-Code → Top-Modell" — so nutzt **Coding automatisch höhere und CI/CD tiefere Modelle**.

---

## 15. Git-Branching-Governance

`aiflow init` / `aiflow change-settings` konfigurieren ein projektbezogenes Branching-Modell. aiflow
leitet ein Governance-Modell in **`.aiflow/branching.json`** + ein lesbares **`docs/branching.md`** ab,
legt die permanenten Branches an, seedet `VERSION` und installiert die Durchsetzung.

- **Modell** — `simple` (main + develop; temporäre Branches beliebig) · `gitflow` (`feature/*` aus
  develop, `hotfix/*` aus main) · `none`.
- **Strikte Regeln** — Branch-Quellen/-Ziele und Benennung erzwingen (gitflow).
- **PR-only** — kein direkter Push auf main/develop; Merge nur über validierten Pull Request.
- **Auto-Release** — ein Merge develop → main erzeugt ein Release.
- **Versionsstrategie** — **SemVer** (`X.Y.0-SNAPSHOT` → `X.Y.0`, dann develop → `X.(Y+1).0-SNAPSHOT`)
  oder **CalVer** (`YYYY.MM`, develop auf nächsten Monat).
- **Release-Tags** — jedes Release taggen (`v1.2.0` / `2026.06`).
- **chore/\*** — chore-Branches (von/nach develop oder main), unabhängig von Feature/Hotfix.

Durchsetzung: der **`pre-push`-Hook** blockt direkte Pushes auf geschützte Branches und lehnt (strikt
gitflow) nicht-konforme Namen ab; **`aiflow protect`** setzt echten server-seitigen Branch-Schutz auf
GitHub (PR + CI erforderlich); **`aiflow release [--push]`** bumpt die Version, taggt und hebt develop
an. Agenten lesen das Modell und befolgen es (CLAUDE.md §7).

---

## 16. Memory

Das Modell vergisst zwischen Sitzungen, daher persistiert aiflow das Wichtige:

- **Beads** — der dauerhafte Task-Store (Abhängigkeiten, Status, Historie).
- **graphify** — strukturelle Karte des Codes (mit `aiflow index` / `/graphify .` bauen); abfragen
  statt neu lesen.
- **`.claude/memory/`** (optional, per Config) — dauerhafte, nicht-offensichtliche Fakten:
  `project-aim.md` (Ziel + Architektur), `dev-environment.md` (OS/IDE/VCS) plus, was `onboard`/
  `explain` lernen. Indiziert in `.claude/MEMORY.md`.

---

## 17. Konfigurationsmodell

Alles wird von **`.aiflow/config.json`** gesteuert (committet; ohne Secrets). Form:

```jsonc
{
  "caveman":   { "enabled": true, "mode": "full" },
  "rtk":       { "enabled": true },
  "router":    { "enabled": false },
  "graphify":  { "enabled": true },
  "taskmaster":{ "enabled": true },
  "mcp":       { "filesystem": true },
  "memory":    { "enabled": true },
  "vcs": "github",
  "project": { "aim": "...", "architecture": "..." },
  "dev": { "os": "windows", "ide": "vscode" },
  "git": { "model": "gitflow", "strict": true, "prOnly": true,
           "autoRelease": true, "versionStrategy": "semver", "releaseTags": true, "chore": true }
}
```

`aiflow change-settings` bearbeitet sie interaktiv und wendet neu an (regeneriert `.mcp.json`, Hooks,
Branching-Modell, Memory usw.). Secrets bleiben stets in `.env` (gitignored).

---

## 18. Tools global, Konfiguration projektspezifisch

- **Tools / Binaries** — einmal pro Benutzer (`npm -g`, `uv tool`, brew/winget); über Projekte
  geteilt. `aiflow install-deps` legt sie dort ab. Die Router-Config liegt ebenfalls im Home.
- **Konfiguration & Secrets** — pro Projekt: `.env` (gitignored, nie global), `.aiflow/config.json`,
  `CLAUDE.md`, `.mcp.json`, `.claude/`, `.githooks/`, Memory. Projektwechsel wechselt die Config;
  nichts leakt zwischen Projekten.

---

## 19. CI/CD

- **`.github/workflows/agent.yml`** — fährt den Headless-Ralph-Loop in CI bei drei Auslösern: manuell
  (mit Prompt), Issue-Label **`agent`** oder nächtlich. Installiert die Toolchain, fährt den Loop,
  pusht einen Branch, öffnet bei `COMPLETE` einen PR und kommentiert das Issue. Auth aus Repo-Secrets
  `ANTHROPIC_API_KEY` **oder** `CLAUDE_CODE_OAUTH_TOKEN`.
- **`.github/workflows/ci.yml`** — erkennt den Stack (Node/Python/Go/Dart) und fährt Format + Tests.

---

## 20. Headless & Container

Der Headless-Ralph-Loop läuft auf zwei Wegen identisch:
- **Direkt:** `aiflow ralph "<task>"` (nutzt lokales claude/Login).
- **Container:** `docker/run.sh "<task>"` — baut `docker/Dockerfile`, mountet Repo, injiziert Tokens.
  Nutzt **Podman oder Docker** (auto-erkannt; überschreiben mit `AIFLOW_CONTAINER=podman|docker`).

---

## 21. Anpassen

- **Regeln für alle Agenten:** `CLAUDE.md` bearbeiten (Überblick §1, Architektur §2, Stil §3,
  Workflow, Git, DoD).
- **Architekturhinweise:** schnelle Regeln in `CLAUDE.md §2`; das große Bild in `docs/architecture/`
  (arc42); Entscheidungen als ADRs (`/arch "<frage>"` schreibt sie via *architect*-Agent).
- **Skills:** `.claude/commands/<name>.md` anlegen.
- **Berechtigungen:** `permissions.allow` in `.claude/settings.json` erlaubt Routine-Befehle vorab
  (alle `bd`, git, Toolchain-CLIs, read-only Shell, Build/Test, Formatter, MCP-Server); eigene dort
  oder in `.claude/settings.local.json` (gitignored), oder zur Laufzeit per `/permissions`. Datei-Edits
  (Edit/Write) sind bewusst nicht vorab erlaubt.
- **High-Level-Schalter:** `aiflow change-settings`.

---

## 22. Projektstruktur

```
CLAUDE.md                  Regeln für alle Agenten (Architektur, Google Style, Workflow, Git, DoD)
README.md / README.de.md   dieses Handbuch (EN/DE)
LICENSE                    MIT
.aiflow/
  config.json              deine Auswahl (committet, keine Secrets) — via change-settings ändern
  branching.json           abgeleitetes Git-Governance-Modell
  ralph-headless.sh        autonomer Loop-Runner
  run-agent.sh             generischer Headless-Agent-Runner (Audits, onboard)
  version.sh, release.sh, protect.sh   Release/Versionierung/Branch-Schutz
  router-config.example.json           claude-code-router Vorlage
.env / .env.example        Tokens (gitignored)
.gitignore .gitattributes  Secrets schützen; LF für Scripts erzwingen
.mcp.json                  generierte MCP-Server (per Config): filesystem + github (vcs=github) + graphify + task-master
.claude/
  settings.json            Berechtigungen + Hooks
  agents/                  architect, planner, implementer, reviewer, tester, security-advisor,
                           quality-check, dependency-auditor, test-gap-advisor, performance-advisor,
                           docs-sync, requirements-check, onboarder
  commands/                intake-issue, decompose, plan-epic, implement, review-ac, arch,
                           security-check, quality-check, requirements-check, dependency-check,
                           test-gap, perf-check, docs-check, onboard, explain, standup
  hooks/                   format.sh (Auto-Format), caveman.sh (knapper Output)
  memory/                  project-aim.md, dev-environment.md, … (wenn Memory an)
.githooks/                 commit-msg (Conventional Commits), pre-commit (Format+Lint+Test), pre-push (Branching)
.github/workflows/         agent.yml (Agent in CI) + ci.yml (Lint/Test)
docs/
  architecture/            arc42 + ADRs
  branching.md             menschenlesbares Branching-Modell
.beads/                    Beads-Task-Store (Dolt-gestützt)
VERSION                    aktuelle Version (wenn Auto-Release an)
```

---

## 23. Aktualisieren

```bash
aiflow upgrade     # aktualisiert claude-code, task-master-ai, claude-code-router, graphify, beads,
                   # rtk auf latest, baut den Graphen neu, wendet deine Config neu an
```
aiflow selbst braucht kein Upgrade-Tool — `upgrade` betrifft die **Dependencies**, die es orchestriert.

---

## 24. Troubleshooting

- **MCP verbindet nicht:** Docker läuft? `GITHUB_TOKEN` in `.env` und über `aiflow shell` gestartet?
  Token-Scopes (repo, issues)?
- **Ralph sofort BLOCKED:** `result.json` / `.aiflow/ralph.log` lesen — meist unklare AK oder
  fehlender Zugang.
- **`bd`-Fehler / keine DB:** Beads braucht **dolt** — `aiflow install-deps` installiert es.
- **Auto-Format/Lint tut nichts:** passenden Formatter installieren (CLAUDE.md §3).
- **pre-push blockt:** das ist das Branching-Modell; ordentlichen Branch/PR nutzen oder
  `AIFLOW_ALLOW_DIRECT_PUSH=1` für Tooling.
- **`--router` startet nicht:** claude-code-router installieren + `~/.claude-code-router/config.json` anlegen.
- **Container-Run schlägt fehl:** sicherstellen, dass Podman oder Docker installiert ist und
  Daemon/Machine läuft; eines erzwingen mit `AIFLOW_CONTAINER=podman|docker`.
- **jq fehlt:** nötig zum Lesen der Config — `aiflow install-deps` installiert es.

---

## 25. Mitwirken

Bug gefunden oder eine Idee? Beiträge sind sehr willkommen.

- **Issues:** unter https://github.com/Cyber93de/aiflow/issues anlegen — mit Reproduktionsschritten
  oder klarer Beschreibung des Features.
- **Pull Requests:** forken → Branch (`feat/…`, `fix/…`) → Änderung → Hooks müssen bestehen
  (Conventional Commits, Format, Lint, Tests) → PR gegen `main`.
- **Stil:** aiflow folgt den eigenen Regeln — Google Style für alle Sprachen, Conventional Commits,
  kleine reviewbare Änderungen. Die `.githooks` des Repos erzwingen das.
- Sei freundlich und konstruktiv. Danke an alle, die das Projekt verbessern.

---

## 26. Feedback, Ideen & Bug-Meldungen

Ideen, Feature-Wünsche, Kritik und Bug-Meldungen sind alle sehr willkommen — so wird aiflow besser.
Eröffne eine [Discussion](https://github.com/Cyber93de/aiflow/discussions) oder ein
[Issue](https://github.com/Cyber93de/aiflow/issues) (für Bugs: Reproschritte + OS + `aiflow
doctor`-Ausgabe). Es gibt keine Bezahlstufe und keine Spendenbitte — ein ⭐ und ehrliches Feedback
sind die beste Unterstützung. Danke!

---

## 27. Lizenz

MIT — Copyright (c) 2026 Cyber93de. Siehe [LICENSE](LICENSE).

---

## Mit aiflow gebaut

Dieses Projekt wurde mit Unterstützung von **[aiflow](https://cyber93de.github.io/aiflow/)** gebaut — *built with aiflow*.
