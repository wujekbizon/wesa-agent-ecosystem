#!/usr/bin/env python3
"""
school_pipeline.py — WESA School Project Code Generation Pipeline

Reads domain.yaml → generates daemon task JSONs → drops into agent-ecosystem/tasks/.
Architecture patterns from ARCHITECTURE.md are injected into every task prompt.
Run one stage at a time — Axel reviews output at each gate before releasing the next.

Usage:
    python scripts/school_pipeline.py --config ../school/domain.yaml --stage 1
    python scripts/school_pipeline.py --config ../school/domain.yaml --stage 1 --dry-run
    python scripts/school_pipeline.py --config ../school/domain.yaml --stage all  # not recommended

Stages:
    1 — AgentHire.Data  (entity classes + DbContext)        → D1
    2 — AgentHire.Interfaces  (service interfaces)          → D1
    3 — AgentHire.Services  (BaseService + implementations) → D1
    4 — AgentHire.Intranet controllers                      → D1
    5 — AgentHire.Intranet views                            → W1 (batched)
    6 — AgentHire.PortalWWW controllers + DI               → D1
    7 — AgentHire.PortalWWW views                          → W1 (batched)

output_file paths are relative to WORKSPACE_ROOT (agent-ecosystem/).
School project files use the ../ prefix: ../school/AgentHire/...
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml --break-system-packages")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_ECOSYSTEM_ROOT = SCRIPT_DIR.parent
TASKS_DIR = AGENT_ECOSYSTEM_ROOT / "tasks"
SCHOOL_DIR = AGENT_ECOSYSTEM_ROOT.parent / "school"
ARCHITECTURE_FILE = SCHOOL_DIR / "ARCHITECTURE.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_domain(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_arch_sections(path: Path) -> dict:
    """
    Parse ARCHITECTURE.md into sections keyed by '## Heading' text.
    Each value is the full text of that section (including the heading line).
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                current = line.strip()[3:]
                sections[current] = [line]
            elif current is not None:
                sections[current].append(line)

    return {k: "".join(v) for k, v in sections.items()}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def school_path(solution: str, *parts: str) -> str:
    """
    Return output_file path relative to WORKSPACE_ROOT (agent-ecosystem/).
    School project files live under ../school/<solution>/.
    Always uses forward slashes — daemon handles Windows paths from there.
    """
    rel = Path("../school") / solution / Path(*parts)
    return str(rel).replace("\\", "/")


def drop_task(task: dict, dry_run: bool = False) -> None:
    tid = task["task_id"]
    out = task.get("output_file", "(no output_file)")
    if dry_run:
        print(f"  [DRY RUN] {tid}  →  {out}")
        return
    path = TASKS_DIR / f"{tid}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)
    print(f"  Queued:   {tid}  →  {out}")


# ---------------------------------------------------------------------------
# Stage 1 — Data entities + DbContext
# ---------------------------------------------------------------------------

def stage_1(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    ctx = domain["solution"]["db_context"]
    entities = domain["entities"]
    pattern = arch.get("Project 1: Firma.Data", "")
    tasks = []

    for ent in entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        fk_parent = ent.get("fk_parent")
        fields = ent["fields"]
        children = [e["name"] for e in entities if e.get("fk_parent") == name]

        field_blocks = []
        for f in fields:
            lines = []
            if f.get("required"):
                lines.append(f'[Required(ErrorMessage = "{f["name"]} is required")]')
            if f.get("max_length"):
                lines.append(f'[MaxLength({f["max_length"]})]')
            lines.append(f'[Display(Name = "{f["name"]}")]')
            if f.get("column"):
                lines.append(f'[Column(TypeName = "{f["column"]}")]')

            ann = "\n        ".join(lines)
            ft = f["type"]
            fn = f["name"]
            req = f.get("required", False)
            col = f.get("column", "")

            if ft == "string":
                if "MAX" in col:
                    prop = f"public required string {fn} {{ get; set; }}"
                elif req:
                    prop = f"public required string {fn} {{ get; set; }}"
                else:
                    prop = f'public string {fn} {{ get; set; }} = string.Empty;'
            elif ft == "decimal":
                prop = f"public decimal {fn} {{ get; set; }}"
            elif ft == "int":
                prop = f"public int {fn} {{ get; set; }}"
            elif ft == "bool":
                default = "true" if f.get("default") else "false"
                prop = f"public bool {fn} {{ get; set; }} = {default};"
            elif ft == "DateTime":
                prop = f"public DateTime {fn} {{ get; set; }}"
            else:
                prop = f"public {ft} {fn} {{ get; set; }}"

            field_blocks.append(f"        {ann}\n        {prop}")

        fk_block = ""
        if fk_parent:
            fk_block = (
                f"\n        [ForeignKey(\"{fk_parent}\")]\n"
                f"        public int Id{fk_parent} {{ get; set; }}\n"
                f"        public {fk_parent}? {fk_parent} {{ get; set; }}"
            )

        child_block = ""
        for child in children:
            child_block += (
                f"\n        public ICollection<{child}> {child} {{ get; }} "
                f"= new List<{child}>();"
            )

        fields_str = "\n\n".join(field_blocks)

        prompt = f"""You are writing a single C# entity class for an ASP.NET Core MVC solution.

ARCHITECTURE REFERENCE — follow this pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Data/Data/{folder}/{name}.cs
Namespace: {ns}.Data.Data.{folder}
Class name: {name}

[Key]
public int {pk} {{ get; set; }}

{fields_str}
{fk_block}
{child_block}

RULES:
- Use System.ComponentModel.DataAnnotations and System.ComponentModel.DataAnnotations.Schema
- Required string fields use the `required` keyword
- Optional string fields default to string.Empty
- Collection navigation properties have no setter: ICollection<T> X {{ get; }} = new List<T>();
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: anything other than {name}.cs
"""
        tasks.append({
            "task_id": new_id(f"school-data-{name.lower()}"),
            "type": "code",
            "prompt": prompt,
            "output_file": school_path(sol, f"{sol}.Data", "Data", folder, f"{name}.cs"),
        })

    # DbContext
    dbsets = "\n        ".join(
        f"public DbSet<{e['name']}> {e['name']} {{ get; set; }} = default!;"
        for e in entities
    )
    usings = "\n".join(sorted({
        f"using {ns}.Data.Data.{e['domain']};"
        for e in entities
    }))

    tasks.append({
        "task_id": new_id("school-data-context"),
        "type": "code",
        "prompt": f"""You are writing the DbContext class for an ASP.NET Core MVC solution.

ARCHITECTURE REFERENCE — follow FirmaContext pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Data/{ctx}.cs
Namespace: {ns}.Data.Data
Class: {ctx} : DbContext
Constructor: public {ctx}(DbContextOptions<{ctx}> options) : base(options) {{ }}

Usings:
{usings}
using Microsoft.EntityFrameworkCore;

DbSet properties:
        {dbsets}

RULES:
- Follow the FirmaContext pattern from the architecture reference exactly
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any entity .cs files
""",
        "output_file": school_path(sol, f"{sol}.Data", f"{ctx}.cs"),
    })

    return tasks


# ---------------------------------------------------------------------------
# Stage 2 — Interfaces
# ---------------------------------------------------------------------------

def stage_2(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    entities = domain["entities"]
    additional = {a["id"]: a for a in domain.get("additional_elements", [])}
    pattern = arch.get("Project 2: Firma.Interfaces", "")
    tasks = []

    portal_entities = [e for e in entities if e.get("portal_exposed")]

    for ent in portal_entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        pk_param = pk[0].lower() + pk[1:]
        fk_parent = ent.get("fk_parent")

        methods = []

        if fk_parent:
            # FK child: only needs GetByParent
            methods.append(f"Task<IList<{name}>> Get{name}sBy{fk_parent}(int id{fk_parent});")
        else:
            methods.append(f"Task<IList<{name}>> Get{name}s();")
            methods.append(f"Task<{name}?> Get{name}(int {pk_param});")

        # Additional element methods
        if "A" in additional and additional["A"]["entity"] == name:
            methods.append(f"Task<IList<{name}>> Get{name}sByQuery(string query);")
        if "G" in additional and additional["G"]["entity"] == name:
            methods.append(f"Task<IList<{name}>> GetHighlighted{name}s();")
        if "F" in additional and additional["F"]["entity"] == name:
            methods.append(f"Task<{name}?> Get{name}WithChildren(int {pk_param});")

        methods_str = "\n        ".join(methods)

        tasks.append({
            "task_id": new_id(f"school-iface-{name.lower()}"),
            "type": "code",
            "prompt": f"""You are writing a C# service interface for an ASP.NET Core MVC solution.

ARCHITECTURE REFERENCE — follow this pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Interfaces/{folder}/I{name}Service.cs
Namespace: {ns}.Interfaces.{folder}
Interface: I{name}Service
Using: using {ns}.Data.Data.{folder};

Methods:
        {methods_str}

RULES:
- All methods return Task<T> or Task<IList<T>> — never IEnumerable, never IQueryable
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any other interface or entity file
""",
            "output_file": school_path(sol, f"{sol}.Interfaces", folder, f"I{name}Service.cs"),
        })

    return tasks


# ---------------------------------------------------------------------------
# Stage 3 — Services
# ---------------------------------------------------------------------------

def stage_3(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    ctx = domain["solution"]["db_context"]
    entities = domain["entities"]
    additional = {a["id"]: a for a in domain.get("additional_elements", [])}
    pattern = arch.get("Project 3: Firma.Services", "")
    tasks = []

    # BaseService
    tasks.append({
        "task_id": new_id("school-svc-base"),
        "type": "code",
        "prompt": f"""You are writing the abstract BaseService class for an ASP.NET Core MVC solution.

ARCHITECTURE REFERENCE — follow BaseService pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Services/Abstrakcja/BaseService.cs
Namespace: {ns}.Services.Abstrakcja
Using: using {ns}.Data.Data;

Abstract class BaseService:
    protected readonly {ctx} _context;
    public BaseService({ctx} context) {{ _context = context; }}

RULES:
- Abstract class only — no business logic here
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any service implementation file
""",
        "output_file": school_path(sol, f"{sol}.Services", "Abstrakcja", "BaseService.cs"),
    })

    portal_entities = [e for e in entities if e.get("portal_exposed")]

    for ent in portal_entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        pk_param = pk[0].lower() + pk[1:]
        fk_parent = ent.get("fk_parent")

        methods = []

        if fk_parent:
            methods.append(f"""public async Task<IList<{name}>> Get{name}sBy{fk_parent}(int id{fk_parent})
        {{
            return await _context.{name}
                .Where(x => x.Id{fk_parent} == id{fk_parent})
                .ToListAsync();
        }}""")
        else:
            methods.append(f"""public async Task<IList<{name}>> Get{name}s()
        {{
            return await _context.{name}.ToListAsync();
        }}""")
            methods.append(f"""public async Task<{name}?> Get{name}(int {pk_param})
        {{
            return await _context.{name}
                .FirstOrDefaultAsync(x => x.{pk} == {pk_param});
        }}""")

        if "A" in additional and additional["A"]["entity"] == name:
            search_fields = additional["A"].get("search_fields", ["Name"])
            where_clause = " || ".join(
                f'x.{sf}.Contains(query)' for sf in search_fields
            )
            methods.append(f"""public async Task<IList<{name}>> Get{name}sByQuery(string query)
        {{
            return await _context.{name}
                .Where(x => {where_clause})
                .ToListAsync();
        }}""")

        if "G" in additional and additional["G"]["entity"] == name:
            methods.append(f"""public async Task<IList<{name}>> GetHighlighted{name}s()
        {{
            return await _context.{name}
                .Where(x => x.IsHighlighted)
                .ToListAsync();
        }}""")

        if "F" in additional and additional["F"]["entity"] == name:
            child = additional["F"].get("child_entity", "")
            child_nav = f".Include(x => x.{child})" if child else ""
            methods.append(f"""public async Task<{name}?> Get{name}WithChildren(int {pk_param})
        {{
            return await _context.{name}
                {child_nav}
                .FirstOrDefaultAsync(x => x.{pk} == {pk_param});
        }}""")

        methods_str = "\n\n        ".join(methods)

        tasks.append({
            "task_id": new_id(f"school-svc-{name.lower()}"),
            "type": "code",
            "prompt": f"""You are writing a service implementation class for an ASP.NET Core MVC solution.

ARCHITECTURE REFERENCE — follow service implementation pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Services/{folder}/{name}Service.cs
Namespace: {ns}.Services.{folder}
Class: {name}Service : BaseService, I{name}Service
Constructor: public {name}Service({ctx} context) : base(context) {{ }}

Required usings:
using {ns}.Data.Data;
using {ns}.Data.Data.{folder};
using {ns}.Interfaces.{folder};
using {ns}.Services.Abstrakcja;
using Microsoft.EntityFrameworkCore;

Method implementations:
        {methods_str}

RULES:
- Inherits BaseService, implements I{name}Service
- All methods async/await
- Always materialize (.ToListAsync(), .FirstOrDefaultAsync()) — never return IQueryable
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: BaseService.cs, any interface file, any entity file
""",
            "output_file": school_path(sol, f"{sol}.Services", folder, f"{name}Service.cs"),
        })

    return tasks


# ---------------------------------------------------------------------------
# Stage 4 — Intranet controllers
# ---------------------------------------------------------------------------

def stage_4(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    ctx = domain["solution"]["db_context"]
    conn = domain["solution"]["connection_string_name"]
    entities = domain["entities"]
    pattern = arch.get("Project 4: Firma.Intranet", "")
    tasks = []

    # Program.cs
    tasks.append({
        "task_id": new_id("school-intranet-program"),
        "type": "code",
        "prompt": f"""You are writing Program.cs for an ASP.NET Core MVC Intranet (admin panel) application.

ARCHITECTURE REFERENCE — follow Firma.Intranet Program.cs pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Intranet/Program.cs
Namespace: {ns}.Intranet
DbContext: {ctx}
Connection string name: "{conn}"
Using: using {ns}.Data.Data;

RULES:
- Register {ctx} with SQL Server — NO service DI (Intranet hits DB directly)
- Standard ASP.NET Core MVC middleware pipeline
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any controller or view file
""",
        "output_file": school_path(sol, f"{sol}.Intranet", "Program.cs"),
    })

    # appsettings.json
    tasks.append({
        "task_id": new_id("school-intranet-appsettings"),
        "type": "code",
        "prompt": f"""Generate appsettings.json for an ASP.NET Core MVC Intranet application.

Connection string name: "{conn}"
Database name: "{ctx}"
Server: (localdb)\\mssqllocaldb
Include: Logging (default levels), AllowedHosts: "*"

Output ONLY the JSON. No markdown fences.
""",
        "output_file": school_path(sol, f"{sol}.Intranet", "appsettings.json"),
    })

    for ent in entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        fk_parent = ent.get("fk_parent")
        intranet_crud = ent.get("intranet_crud", True)
        fields = ent["fields"]

        if not intranet_crud:
            # Read-only controller (e.g. Inquiry)
            tasks.append({
                "task_id": new_id(f"school-intranet-ctrl-{name.lower()}"),
                "type": "code",
                "prompt": f"""You are writing a read-only Intranet controller for an ASP.NET Core MVC application.

ARCHITECTURE REFERENCE:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Intranet/Controllers/{name}Controller.cs
Namespace: {ns}.Intranet.Controllers
Injects: {ctx} _context (direct DB access — no service layer in Intranet)
Entity: {name} | PK: {pk} | Domain: {folder}

Implement only:
- Index(): return all {name} records ordered by {pk} descending
- Details(int? id): single record or NotFound()

Required usings:
using {ns}.Data.Data;
using {ns}.Data.Data.{folder};

RULES:
- Constructor injects {ctx} directly
- No Create/Edit/Delete actions
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any other controller, view, or entity file
""",
                "output_file": school_path(sol, f"{sol}.Intranet", "Controllers", f"{name}Controller.cs"),
            })
        else:
            bind_fields = [pk] + [f["name"] for f in fields]
            if fk_parent:
                bind_fields.append(f"Id{fk_parent}")
            bind_str = ",".join(bind_fields)

            fk_notes = ""
            if fk_parent:
                fk_notes = f"""
FK relationship: {name} belongs to {fk_parent}
- Index: use .Include(e => e.{fk_parent}) to load parent name
- Create GET: ViewData["Id{fk_parent}"] = new SelectList(_context.{fk_parent}, "Id{fk_parent}", "Name")
- Create POST on failure: repopulate ViewData["Id{fk_parent}"]
- Edit GET: ViewData["Id{fk_parent}"] = new SelectList(..., selected: entity.Id{fk_parent})
- Edit POST on failure: repopulate ViewData["Id{fk_parent}"]"""

            bool_fields = [f["name"] for f in fields if f["type"] == "bool"]
            bool_note = f"Has boolean field(s): {', '.join(bool_fields)} — Intranet Create/Edit will need checkbox input." if bool_fields else ""

            tasks.append({
                "task_id": new_id(f"school-intranet-ctrl-{name.lower()}"),
                "type": "code",
                "prompt": f"""You are writing a full CRUD controller for an ASP.NET Core MVC Intranet (admin panel) application.

ARCHITECTURE REFERENCE — follow TowarController or AktualnoscController pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.Intranet/Controllers/{name}Controller.cs
Namespace: {ns}.Intranet.Controllers
Injects: {ctx} _context (direct DB access — no service layer in Intranet)
Entity: {name} | PK: {pk} | Domain: {folder}
[Bind] fields: "{bind_str}"
{fk_notes}
{bool_note}

Implement all 7 members:
1. Index (GET)
2. Details (GET) — id? nullable, NotFound if null or missing
3. Create (GET)
4. Create (POST) — [HttpPost][ValidateAntiForgeryToken]
5. Edit (GET)
6. Edit (POST) — [HttpPost][ValidateAntiForgeryToken] + DbUpdateConcurrencyException handling
7. Delete (GET)
8. DeleteConfirmed (POST) — [HttpPost, ActionName("Delete")][ValidateAntiForgeryToken]
9. private bool {name}Exists(int id)

Required usings:
using {ns}.Data.Data;
using {ns}.Data.Data.{folder};

RULES:
- Constructor injects {ctx} directly
- Edit POST must catch DbUpdateConcurrencyException: if !{name}Exists → NotFound, else throw
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any other controller, view, or entity file
""",
                "output_file": school_path(sol, f"{sol}.Intranet", "Controllers", f"{name}Controller.cs"),
            })

    return tasks


# ---------------------------------------------------------------------------
# Stage 5 — Intranet views (W1, batched)
# ---------------------------------------------------------------------------

def stage_5(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    entities = domain["entities"]
    pattern = arch.get("Project 4: Firma.Intranet", "")
    tasks = []

    # Domain grouping for nav
    domains = {}
    for e in entities:
        domains.setdefault(e["domain"], []).append(e["name"])
    nav_groups = "\n".join(
        f"  {d}: {', '.join(names)}" for d, names in domains.items()
    )

    # Shared layout + _ViewImports + _ViewStart
    tasks.append({
        "task_id": new_id("school-intranet-shared"),
        "type": "write",
        "prompt": f"""You are writing shared Razor files for an ASP.NET Core MVC Intranet (admin panel) application.

ARCHITECTURE REFERENCE — follow Intranet view patterns exactly:
{pattern}

Create these files for {ns}.Intranet:

FILE 1: Views/Shared/_Layout.cshtml
- Custom CSS navbar (.navbar class in site.css — no Bootstrap), app name "AgentHire Admin"
- Navigation links to all entity controllers, grouped by domain:
{nav_groups}
- Footer: © AgentHire Admin
- Standard @RenderBody() and @RenderSection("Scripts", required: false)

FILE 2: Views/Shared/_ValidationScriptsPartial.cshtml
- Renders jquery-validation and jquery-validation-unobtrusive scripts
- Match the pattern from ARCHITECTURE.md exactly

FILE 3: Views/Shared/Error.cshtml
- Standard error view using ErrorViewModel (model has RequestId property)

FILE 4: _ViewImports.cshtml (project root)
- @using {ns}.Intranet
- @using {ns}.Intranet.Models
- @addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers

FILE 5: _ViewStart.cshtml (project root)
- Layout = "~/Views/Shared/_Layout.cshtml"

RULES:
- Custom CSS throughout (wwwroot/css/site.css — no Bootstrap CDN)
- Output each file preceded by a line: // FILE: <path>
- No hardcoded display strings — any text that appears in UI uses variables or is structural UI chrome
""",
        "output_file": school_path(sol, f"{sol}.Intranet", "Views", "Shared", "_Layout.cshtml"),
    })

    # Per-entity view batches
    for ent in entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        fields = ent["fields"]
        fk_parent = ent.get("fk_parent")
        intranet_crud = ent.get("intranet_crud", True)

        fields_desc = "\n".join(
            f"  - {f['name']}: {f['type']}"
            + (f" (max {f['max_length']})" if f.get("max_length") else "")
            + (" [required]" if f.get("required") else "")
            + (" [FK dropdown]" if f["name"] == f"Id{fk_parent}" else "")
            for f in fields
        )
        if fk_parent:
            fields_desc += f"\n  - Id{fk_parent}: int [FK dropdown → {fk_parent}.Name]"

        bool_fields = [f["name"] for f in fields if f["type"] == "bool"]

        if not intranet_crud:
            views_needed = "Index.cshtml and Details.cshtml only (read-only — no Create/Edit/Delete)"
        else:
            views_needed = "Index.cshtml, Create.cshtml, Edit.cshtml, Details.cshtml, Delete.cshtml"

        tasks.append({
            "task_id": new_id(f"school-intranet-views-{name.lower()}"),
            "type": "write",
            "prompt": f"""You are writing Razor CRUD views for the {name} entity in an ASP.NET Core MVC Intranet application.

ARCHITECTURE REFERENCE — follow Intranet view patterns exactly:
{pattern}

Entity: {name}
Full type: {ns}.Data.Data.{folder}.{name}
PK: {pk}
Fields:
{fields_desc}
{"FK parent: " + fk_parent + " — Create/Edit need <select asp-items> dropdown" if fk_parent else "No FK relationships"}
{"Boolean field(s): " + ", ".join(bool_fields) + " — Create/Edit need <input type='checkbox' asp-for=...>" if bool_fields else ""}

Views to create in Views/{name}/:
{views_needed}

RULES for all views:
- Typed @model: IEnumerable<...{name}> for Index, ...{name} for others
- Custom CSS: .table class for Index, .form-card class for Create/Edit (centered, max-width: 600px)
- Action links as custom CSS buttons: .btn .btn-sm .btn-outline / .btn-danger
- asp-for inputs + asp-validation-for spans in Create/Edit
- All Create/Edit end with: @section Scripts {{ @{{await Html.RenderPartialAsync("_ValidationScriptsPartial");}} }}
- Delete.cshtml shows entity summary + btn-danger "Delete" confirm button
- Details.cshtml uses custom CSS .card layout with dl/dt/dd
- Output each file preceded by: // FILE: Views/{name}/<filename>
""",
            "output_file": school_path(sol, f"{sol}.Intranet", "Views", name, "Index.cshtml"),
        })

    return tasks


# ---------------------------------------------------------------------------
# Stage 6 — Portal controllers + DI factory
# ---------------------------------------------------------------------------

def stage_6(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    ctx = domain["solution"]["db_context"]
    conn = domain["solution"]["connection_string_name"]
    entities = domain["entities"]
    additional = {a["id"]: a for a in domain.get("additional_elements", [])}
    pattern = arch.get("Project 5: Firma.PortalWWW", "")
    tasks = []

    pages_ent = next((e for e in entities if e.get("cms_role") == "pages"), None)
    news_ent = next((e for e in entities if e.get("cms_role") == "news"), None)
    portal_entities = [e for e in entities if e.get("portal_exposed")]
    viewcomp_applies = additional.get("D", {}).get("applies_to_controllers", [])

    # Program.cs
    tasks.append({
        "task_id": new_id("school-portal-program"),
        "type": "code",
        "prompt": f"""You are writing Program.cs for an ASP.NET Core MVC PortalWWW (public-facing) application.

ARCHITECTURE REFERENCE — follow Firma.PortalWWW Program.cs pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Program.cs
Namespace: {ns}.PortalWWW
DbContext: {ctx}
Connection string name: "{conn}"
DI call: DependencyInjectionFactory.Resolve(builder.Services, builder.Configuration)

RULES:
- Register {ctx} with SQL Server
- Call DependencyInjectionFactory.Resolve() — all service registrations live there
- Standard MVC middleware pipeline
- Output ONLY the C# file. No markdown fences. No explanation.
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "Program.cs"),
    })

    # DependencyInjectionFactory
    scoped = "\n            ".join(
        f"services.AddScoped<I{e['name']}Service, {e['name']}Service>();"
        for e in portal_entities
        if e.get("intranet_crud", True)  # Inquiry service not needed (direct context write)
    )
    svc_usings_iface = "\n".join(sorted({
        f"using {ns}.Interfaces.{e['domain']};" for e in portal_entities if e.get("intranet_crud", True)
    }))
    svc_usings_impl = "\n".join(sorted({
        f"using {ns}.Services.{e['domain']};" for e in portal_entities if e.get("intranet_crud", True)
    }))

    tasks.append({
        "task_id": new_id("school-portal-di"),
        "type": "code",
        "prompt": f"""You are writing DependencyInjectionFactory.cs for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow DependencyInjectionFactory pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/DependencyInjectionFactory.cs
Namespace: {ns}.PortalWWW
Static class: DependencyInjectionFactory

Required usings:
{svc_usings_iface}
{svc_usings_impl}

Registrations in Resolve(IServiceCollection services, IConfiguration config):
            {scoped}

RULES:
- Static class with single static Resolve() method
- AddScoped<IXxxService, XxxService>() for each service
- Output ONLY the C# file. No markdown fences. No explanation.
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "DependencyInjectionFactory.cs"),
    })

    # appsettings.json
    tasks.append({
        "task_id": new_id("school-portal-appsettings"),
        "type": "code",
        "prompt": f"""Generate appsettings.json for an ASP.NET Core MVC PortalWWW application.
Connection string name: "{conn}"
Database name: "{ctx}"
Server: (localdb)\\mssqllocaldb
Include: Logging (default levels), AllowedHosts: "*"
Output ONLY the JSON. No markdown fences.
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "appsettings.json"),
    })

    # HomeController
    featured_line = ""
    featured_inject = ""
    if "G" in additional:
        g = additional["G"]
        ent_name = g["entity"]
        vbkey = g.get("homepage_viewbag_key", "FeaturedAgents")
        featured_inject = f", I{ent_name}Service _{ent_name[0].lower() + ent_name[1:]}Service"
        featured_line = f"ViewBag.{vbkey} = await _{ent_name[0].lower() + ent_name[1:]}Service.GetHighlighted{ent_name}s();"

    tasks.append({
        "task_id": new_id("school-portal-ctrl-home"),
        "type": "code",
        "prompt": f"""You are writing HomeController for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow PortalWWW HomeController pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Controllers/HomeController.cs
Namespace: {ns}.PortalWWW.Controllers

Constructor injects:
- I{pages_ent['name']}Service _pageService
- I{news_ent['name']}Service _newsService
{featured_inject}

Index(int? id) action:
- ViewBag.ModelPages = await _pageService.Get{pages_ent['name']}s()
- ViewBag.ModelNews = await _newsService.GetNewsItems(3)   // or GetNewsItemsByPositionTake(3)
- {featured_line}
- if id == null → id = 1
- var page = await _pageService.Get{pages_ent['name']}(id)
- return View(page)

RULES:
- Every action must populate ViewBag.ModelPages and ViewBag.ModelNews before returning
- Output ONLY the C# file. No markdown fences. No explanation.
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "Controllers", "HomeController.cs"),
    })

    # Per-entity Portal controllers (skip CMS entities — handled by Home/News controllers)
    for ent in portal_entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        pk_param = pk[0].lower() + pk[1:]
        fk_parent = ent.get("fk_parent")
        intranet_crud = ent.get("intranet_crud", True)
        cms_role = ent.get("cms_role")
        svc_var = f"_{name[0].lower() + name[1:]}Service"

        if cms_role == "pages":
            continue  # handled by HomeController

        if cms_role == "news":
            # Simple news controller
            tasks.append({
                "task_id": new_id("school-portal-ctrl-news"),
                "type": "code",
                "prompt": f"""You are writing a NewsController for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow AktualnoscController pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Controllers/NewsController.cs
Namespace: {ns}.PortalWWW.Controllers

Constructor injects:
- I{name}Service {svc_var}
- I{pages_ent['name']}Service _pageService

Index(int id) action:
- ViewBag.ModelPages = await _pageService.Get{pages_ent['name']}s()
- ViewBag.ModelNews = await {svc_var}.Get{name}s()
- var item = await {svc_var}.Get{name}(id)
- if item == null → NotFound()
- return View(item)

RULES:
- Every action populates ViewBag.ModelPages and ViewBag.ModelNews
- Inject service interfaces only — never {ctx} directly
- Output ONLY the C# file. No markdown fences. No explanation.
""",
                "output_file": school_path(sol, f"{sol}.PortalWWW", "Controllers", "NewsController.cs"),
            })
            continue

        uses_agents_layout = name in viewcomp_applies
        has_search = "A" in additional and additional["A"]["entity"] == name
        details_method = (
            f"Get{name}WithChildren"
            if ("F" in additional and additional["F"]["entity"] == name)
            else f"Get{name}"
        )

        if not intranet_crud:
            # Contact form (Inquiry)
            bind_fields = [pk] + [f["name"] for f in ent["fields"]]
            bind_str = ",".join(bind_fields)
            tasks.append({
                "task_id": new_id(f"school-portal-ctrl-{name.lower()}"),
                "type": "code",
                "prompt": f"""You are writing a contact form controller for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow PortalWWW controller + Zapytanie pattern:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Controllers/{name}Controller.cs
Namespace: {ns}.PortalWWW.Controllers

Constructor injects:
- I{pages_ent['name']}Service _pageService
- I{news_ent['name']}Service _newsService
- {ctx} _context  (direct write — Inquiry is the one Portal write exception)

Actions:
- Index (GET): populate ViewBag layout data, return View(new {name}())
- Index (POST) [Bind("{bind_str}")] {name} model:
    if ModelState.IsValid:
        model.SubmittedAt = DateTime.Now
        _context.Add(model)
        await _context.SaveChangesAsync()
        return RedirectToAction(nameof(Index))  // or confirmation page
    else:
        repopulate ViewBag, return View(model)

RULES:
- Every action populates ViewBag.ModelPages and ViewBag.ModelNews
- Output ONLY the C# file. No markdown fences. No explanation.
""",
                "output_file": school_path(sol, f"{sol}.PortalWWW", "Controllers", f"{name}Controller.cs"),
            })
        else:
            layout_note = (
                f"This controller's views use _AgentsLayout.cshtml (ViewComponent handles sidebar).\n"
                f"Do NOT populate ViewBag.ModelPages / ViewBag.ModelNews in Index — not needed for this layout."
                if uses_agents_layout else
                "Every action must populate ViewBag.ModelPages and ViewBag.ModelNews before returning."
            )
            search_param = "string? searchQuery = null" if has_search else ""
            search_logic = (
                f"""
        IList<{name}> items;
        if (!string.IsNullOrWhiteSpace(searchQuery))
            items = await {svc_var}.Get{name}sByQuery(searchQuery);
        else
            items = await {svc_var}.Get{name}s();
        ViewBag.SearchQuery = searchQuery;"""
                if has_search else
                f"\n        var items = await {svc_var}.Get{name}s();"
            )
            inject_lines = [f"I{name}Service {svc_var}"]
            if not uses_agents_layout:
                inject_lines += [
                    f"I{pages_ent['name']}Service _pageService",
                    f"I{news_ent['name']}Service _newsService",
                ]

            tasks.append({
                "task_id": new_id(f"school-portal-ctrl-{name.lower()}"),
                "type": "code",
                "prompt": f"""You are writing a Portal controller for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow PortalWWW controller pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Controllers/{name}Controller.cs
Namespace: {ns}.PortalWWW.Controllers

Constructor injects:
- {chr(10).join("- " + l for l in inject_lines)}

Index({search_param}) action:{search_logic}
    return View(items)

Details(int {pk_param}) action:
    var item = await {svc_var}.{details_method}({pk_param})
    if item == null → NotFound()
    return View(item)

{layout_note}

RULES:
- Inject service interfaces only — never {ctx} directly
- Return NotFound() for null single-item lookups
- Output ONLY the C# file. No markdown fences. No explanation.
- FILES NOT TO TOUCH: any other controller, DI factory, or view file
""",
                "output_file": school_path(sol, f"{sol}.PortalWWW", "Controllers", f"{name}Controller.cs"),
            })

    # ViewComponent class
    if "D" in additional:
        d = additional["D"]
        comp_name = d["component_name"]
        comp_ent = d["entity"]
        comp_svc_var = f"_{comp_ent[0].lower() + comp_ent[1:]}Service"
        tasks.append({
            "task_id": new_id("school-portal-viewcomp"),
            "type": "code",
            "prompt": f"""You are writing a ViewComponent class for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow RodzajeMenuComponent pattern exactly:
{pattern}

--- YOUR TASK ---
File to create: {ns}.PortalWWW/Controllers/{comp_name}.cs
Namespace: {ns}.PortalWWW.Controllers
Class: {comp_name} : ViewComponent

Constructor injects: I{comp_ent}Service {comp_svc_var}

InvokeAsync():
    return View("{comp_name}", await {comp_svc_var}.Get{comp_ent}s())

RULES:
- Inherits ViewComponent (not Controller)
- InvokeAsync returns Task<IViewComponentResult>
- Output ONLY the C# file. No markdown fences. No explanation.
""",
            "output_file": school_path(sol, f"{sol}.PortalWWW", "Controllers", f"{comp_name}.cs"),
        })

    return tasks


# ---------------------------------------------------------------------------
# Stage 7 — Portal views (W1, batched)
# ---------------------------------------------------------------------------

def stage_7(domain: dict, arch: dict) -> list[dict]:
    sol = domain["solution"]["name"]
    ns = domain["solution"]["namespace_root"]
    entities = domain["entities"]
    additional = {a["id"]: a for a in domain.get("additional_elements", [])}
    pattern = arch.get("Project 5: Firma.PortalWWW", "")
    tasks = []

    pages_ent = next((e for e in entities if e.get("cms_role") == "pages"), None)
    news_ent = next((e for e in entities if e.get("cms_role") == "news"), None)
    portal_entities = [e for e in entities if e.get("portal_exposed") and not e.get("cms_role")]
    viewcomp_name = additional.get("D", {}).get("component_name") if "D" in additional else None
    viewcomp_applies = additional.get("D", {}).get("applies_to_controllers", []) if "D" in additional else []

    # Layouts + partials
    tasks.append({
        "task_id": new_id("school-portal-layouts"),
        "type": "write",
        "prompt": f"""You are writing shared layouts and partial views for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow Firma.PortalWWW layout patterns exactly:
{pattern}

Create these files for {ns}.PortalWWW:

FILE: Views/Shared/_Layout.cshtml
- Custom CSS responsive layout (site.css — no Bootstrap), app name "AgentHire Portal"
- Dynamic navbar: @await Html.PartialAsync("PagesPartial", (IEnumerable<{ns}.Data.Data.CMS.{pages_ent['name']}>)ViewBag.ModelPages)
- News sidebar: @await Html.PartialAsync("NewsPartial", (IEnumerable<{ns}.Data.Data.CMS.{news_ent['name']}>)ViewBag.ModelNews)
- @RenderBody() and @RenderSection("Scripts", required: false)

FILE: Views/Shared/_AgentsLayout.cshtml
- Secondary layout for Agent section pages (Req D)
- Sidebar column: @await Component.InvokeAsync("{viewcomp_name}") instead of ViewBag partials
- Main content column: @RenderBody()
- Custom CSS 2-column grid: sidebar 25% / content 75% using flexbox (site.css .layout-sidebar)

FILE: Views/Shared/PagesPartial.cshtml
- @model IEnumerable<{ns}.Data.Data.CMS.{pages_ent['name']}>
- Renders nav links: asp-controller="Home" asp-action="Index" asp-route-id="@item.{pages_ent['pk']}"
- Text: @item.NavTitle

FILE: Views/Shared/NewsPartial.cshtml
- @model IEnumerable<{ns}.Data.Data.CMS.{news_ent['name']}>
- Compact news list with title and link

FILE: _ViewImports.cshtml (project root)
- @using {ns}.PortalWWW
- @using {ns}.PortalWWW.Models
- @addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers

FILE: _ViewStart.cshtml (project root)
- Layout = "~/Views/Shared/_Layout.cshtml"

RULES:
- Custom CSS throughout (wwwroot/css/site.css — no Bootstrap CDN). No hardcoded display strings.
- Output each file preceded by: // FILE: <path>
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "Views", "Shared", "_Layout.cshtml"),
    })

    # Home page (hero + featured agents)
    featured_ent = additional.get("G", {}).get("entity") if "G" in additional else None
    featured_vbkey = additional.get("G", {}).get("homepage_viewbag_key", "FeaturedAgents") if "G" in additional else None
    featured_type = next((e for e in entities if e["name"] == featured_ent), None) if featured_ent else None

    tasks.append({
        "task_id": new_id("school-portal-views-home"),
        "type": "write",
        "prompt": f"""You are writing the home page view for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE:
{pattern}

FILE: Views/Home/Index.cshtml
@model {ns}.Data.Data.CMS.{pages_ent['name']}  (main page content)

Sections:
1. Hero / banner
   - Headline: @Model.Title
   - Body text: @Model.Content (or a truncated version)
   - CTA button: "Browse Agents" → AgentController Index

{"2. Featured Agents section (Req G)" if featured_ent else ""}
{"   - Data: ViewBag." + featured_vbkey + " (IList<" + ns + ".Data.Data." + (featured_type["domain"] if featured_type else "Agents") + "." + (featured_ent or "") + ">)" if featured_ent else ""}
{"   - Custom CSS card grid (3 per row, .card-grid class): FullName, AvatarUrl (as <img>), HourlyRate" if featured_ent else ""}
{"   - Each card links to Agent Details" if featured_ent else ""}

RULES:
- No hardcoded text — all content from @Model or ViewBag
- Custom CSS (wwwroot/css/site.css — no Bootstrap)
- Output ONLY the view content preceded by: // FILE: Views/Home/Index.cshtml
""",
        "output_file": school_path(sol, f"{sol}.PortalWWW", "Views", "Home", "Index.cshtml"),
    })

    # Per-entity portal views
    for ent in portal_entities:
        name = ent["name"]
        folder = ent["domain"]
        pk = ent["pk"]
        fields = ent["fields"]
        fk_parent = ent.get("fk_parent")
        intranet_crud = ent.get("intranet_crud", True)

        has_search = "A" in additional and additional["A"]["entity"] == name
        has_related = "F" in additional and additional["F"]["entity"] == name
        uses_agents_layout = name in viewcomp_applies
        child_ent = additional.get("F", {}).get("child_entity") if has_related else None

        fields_desc = "\n".join(f"  - {f['name']} ({f['type']})" for f in fields)

        layout_note = (
            f'Use _AgentsLayout: declare @{{ Layout = "~/Views/Shared/_AgentsLayout.cshtml"; }} at top of each view.'
            if uses_agents_layout else
            "Uses default _Layout.cshtml (no layout declaration needed)."
        )

        if not intranet_crud:
            # Contact form
            form_fields = [f for f in fields if f["name"] not in [pk, "SubmittedAt"]]
            form_desc = "\n".join(f"  - {f['name']}: {f['type']}" for f in form_fields)
            tasks.append({
                "task_id": new_id(f"school-portal-views-{name.lower()}"),
                "type": "write",
                "prompt": f"""You are writing the contact form view for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE:
{pattern}

FILE: Views/{name}/Index.cshtml
@model {ns}.Data.Data.{folder}.{name}

Custom CSS centered form (.form-card class, max-width: 600px). Fields:
{form_desc}
(Do NOT show {pk} or SubmittedAt — set server-side)

Submit button: .btn .btn-primary "Send Inquiry"
@section Scripts with _ValidationScriptsPartial

RULES:
- asp-for inputs + asp-validation-for spans for all fields
- No hardcoded text
- Output ONLY the view preceded by: // FILE: Views/{name}/Index.cshtml
""",
                "output_file": school_path(sol, f"{sol}.PortalWWW", "Views", name, "Index.cshtml"),
            })
        else:
            related_note = (
                f"\nDetails.cshtml also renders child {child_ent} records below the parent card.\n"
                f"Children available via @Model.{child_ent} navigation property. Render as custom CSS card grid (.card-grid class)."
                if has_related and child_ent else ""
            )
            tasks.append({
                "task_id": new_id(f"school-portal-views-{name.lower()}"),
                "type": "write",
                "prompt": f"""You are writing Portal views for the {name} entity in an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow PortalWWW view patterns exactly:
{pattern}

Entity: {name} | Namespace: {ns}.Data.Data.{folder} | PK: {pk}
Fields:
{fields_desc}
{"FK parent: " + fk_parent if fk_parent else "No FK"}
{layout_note}

FILE: Views/{name}/Index.cshtml
- @model IEnumerable<{ns}.Data.Data.{folder}.{name}>
{"- Search form: <form method='get'> with <input name='searchQuery'> pre-filled from ViewBag.SearchQuery, submit btn" if has_search else ""}
- Custom CSS card grid (3 per row, .card-grid class), key fields displayed, link to Details

FILE: Views/{name}/Details.cshtml
- @model {ns}.Data.Data.{folder}.{name}
- Full custom CSS .card with all fields{related_note}
- Back button: btn btn-outline-secondary → Index
{related_note}

RULES:
- Custom CSS (wwwroot/css/site.css — no Bootstrap). No hardcoded display strings.
- Output each file preceded by: // FILE: Views/{name}/<filename>
""",
                "output_file": school_path(sol, f"{sol}.PortalWWW", "Views", name, "Index.cshtml"),
            })

    # ViewComponent template
    if "D" in additional:
        d = additional["D"]
        comp_name = d["component_name"]
        comp_ent = d["entity"]
        comp_ent_obj = next((e for e in entities if e["name"] == comp_ent), None)
        comp_folder = comp_ent_obj["domain"] if comp_ent_obj else "Agents"

        tasks.append({
            "task_id": new_id("school-portal-viewcomp-template"),
            "type": "write",
            "prompt": f"""You are writing a ViewComponent Razor template for an ASP.NET Core MVC PortalWWW application.

ARCHITECTURE REFERENCE — follow RodzajeMenuComponent template pattern exactly:
{pattern}

FILE: Views/Agent/Components/{comp_name}/{comp_name}.cshtml
CRITICAL: The path must be exactly this — ViewComponent templates must be at Views/<section>/Components/<Name>/<Name>.cshtml

@model IEnumerable<{ns}.Data.Data.{comp_folder}.{comp_ent}>

Render as Custom CSS sidebar nav list (.sidebar-nav class).
Each item links to: asp-controller="{comp_ent}" asp-action="Index" (or Details with id)
Text: @item.Name

RULES:
- Follow exact ViewComponent template path convention from architecture reference
- No hardcoded text
- Output ONLY the view preceded by: // FILE: Views/Agent/Components/{comp_name}/{comp_name}.cshtml
""",
            "output_file": school_path(
                sol,
                f"{sol}.PortalWWW", "Views", "Agent",
                "Components", comp_name, f"{comp_name}.cshtml"
            ),
        })

    return tasks


# ---------------------------------------------------------------------------
# Stage registry + main
# ---------------------------------------------------------------------------

STAGES = {
    1: ("AgentHire.Data — entity classes + DbContext", stage_1),
    2: ("AgentHire.Interfaces — service interfaces", stage_2),
    3: ("AgentHire.Services — BaseService + implementations", stage_3),
    4: ("AgentHire.Intranet — Program.cs + CRUD controllers", stage_4),
    5: ("AgentHire.Intranet — Razor views (W1)", stage_5),
    6: ("AgentHire.PortalWWW — Program.cs + DI + controllers", stage_6),
    7: ("AgentHire.PortalWWW — Razor views (W1)", stage_7),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WESA School Pipeline — generates daemon task JSONs for a .NET school project."
    )
    parser.add_argument("--config", required=True, help="Path to domain.yaml")
    parser.add_argument(
        "--stage", required=True,
        help="Stage number 1-7, or 'all' to generate everything at once (not recommended)"
    )
    parser.add_argument("--dry-run", action="store_true", help="List tasks without writing files")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)

    if not ARCHITECTURE_FILE.exists():
        print(f"ERROR: ARCHITECTURE.md not found at: {ARCHITECTURE_FILE}")
        sys.exit(1)

    domain = load_domain(config_path)
    arch = load_arch_sections(ARCHITECTURE_FILE)

    if args.stage == "all":
        stage_nums = list(STAGES.keys())
    else:
        try:
            stage_nums = [int(args.stage)]
        except ValueError:
            print(f"ERROR: --stage must be a number 1-7 or 'all'. Got: {args.stage}")
            sys.exit(1)
        if stage_nums[0] not in STAGES:
            print(f"ERROR: Stage {stage_nums[0]} does not exist. Valid: 1-7")
            sys.exit(1)

    if not args.dry_run:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    for n in stage_nums:
        label, fn = STAGES[n]
        print(f"\n=== Stage {n}: {label} ===")
        tasks = fn(domain, arch)
        for t in tasks:
            drop_task(t, dry_run=args.dry_run)
            total += 1

    action = "listed (dry run)" if args.dry_run else "written to tasks/ — daemon picks up within 3s"
    print(f"\nDone. {total} task(s) {action}.")


if __name__ == "__main__":
    main()
