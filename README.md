**Disclosure Pilot AI**

Disclosure Pilot AI is a full-stack desktop application that demonstrates using Large Language Models to automate the drafting, compilation, and iXBRL compliance tagging of SEC quarterly Form 10-Q and yearly Form 10-K financial reports. 

> For comprehensive documentation and visual guides, see [`docs/Disclosure-Pilot-AI.pdf`].

**Key Features**

• Automated Filing Assembly: Assembles modular document components (Cover Page, Financial Statements, MD&A, Notes) into master .docx reports and EDGAR-compliant HTML files. 

• AI Financial Agents: Employs local ChatOllama agents via LangGraph to perform financial calculations, enforce SEC compliance, and draft narrative sections. 

• iXBRL Integration: Converts HTML filings into Inline XBRL (iXBRL) format validated against US-GAAP taxonomies using Arelle. 

**Tech Stack**

| Category | Technologies / Tools |
| --- | --- |
| **Frontend** | React 18, Vite, Recharts |
| **Backend** | FastAPI, Uvicorn, Python 3.13 |
| **Database** | PostgreSQL 18 (read-only financial ledger) |
| **AI & Agent Orchestration** | ChatOllama, LangGraph, LangChain |
| **Document Processing** | `python-docx`, `docxcompose`, custom iXBRL postprocessor (`xbrl_tagger.py`) |
| **iXBRL Viewer** | Arelle validator/viewer |
| **CLI** | Windows PowerShell scripts |

> For full installation instructions, see [`docs/installation/Start-Here-Setup.docx`].

**Prerequisites**

* **OS:** Windows 11
* **Python:** Version 3.13 and added to system `PATH`
* **Node.js:** Installed and nodejs added to system `PATH`
* **PostgreSQL:** Running local instance and bin added to system `PATH`
* **Ollama:** Running service with local LLM models pre-loaded and ollama.exe added to system `PATH`
* **Arelle GUI:** Installed and arelleGUI.exe added to system `PATH`

---

**Installation & Setup**

1. **Clone the Repository**
Open Windows PowerShell (standard user, non-admin) and run:
```powershell
git clone https://github.com/p-dirac/disclosure-pilot-ai
cd disclosure-pilot-ai
```

2. **Populate Database**
Open pgAdmin, launch the Query Tool for your target database, and execute the following SQL scripts to set up the tables and schema:
* `db-applogins.sql`
* `db-bookkeeper.sql`

3. **Extract AppIO Files**
Unpack and install the required `AppIO` files from `AppIO-latest.zip` into the working directory.

4. **Configure Environment Variables**
Create the root `.env` file using the provided template:
```powershell
Copy-Item .env.example .env
```

*Edit `.env` to set your PostgreSQL connection details, local Ollama model parameters, and JWT secret key.*

5. **Unblock Setup Scripts**
Allow execution of local PowerShell scripts:
```powershell
Unblock-File .\unlock.ps1
```

6. **Install & Compile**
Run the automated installer to set up the Python virtual environment (`backend\.venv`), install backend dependencies, compile the React frontend, and stage build files into `backend\static`:
```powershell
.\install.ps1
```

**Running the Project**

1. **Verify Connections**
Run the preflight connectivity checker to confirm that PostgreSQL, Ollama, and Arelle endpoints are reachable:
```powershell
.\check-connections.ps1
```

2. **Launch Application**
Start the FastAPI web server. This will automatically open the application in your default browser at `http://localhost:8000`:
```powershell
.\run.ps1
```

3. **Log In**
Use the default test credentials to authenticate:
   * **Email:** `zx@qaz.com`
   * **Password:** `qwerty`

4. **Menu Structure**
Follow this workflow: Prep → Report → EDGAR.
   * **Home** 
   * **10-Q:** `Prep, Report, EDGAR`
   * **10-K:** `Prep, Report, EDGAR`
   * **Help:** `User Guide, About`


**Directory Architecture**

• Application Repository:

```
disclosure-pilot-ai/
├── backend/
│   ├── app/
│   │   ├── agents/    # AI agents 
│   │   ├── api/       # FastAPI route endpoints
│   │   ├── core/      # App configurations
│   │   ├── models/    # User auth database
│   │   ├── schemas/   # Input/output validation
│   │   └── services/  # Core business logic layer
│   └── tests/      # Backend unit tests
├── docs/       # Project description & setup
└── frontend/
	├── dist/     # Index files
	└── src/
		├── api/         # Axios clients & HTTP 
		├── components/  # UI menus, tables, dialogs
		├── hooks/       # User auth context
		├── pages/       # Top-level views
		├── styles/      # Global CSS module
		└── tests/       # Frontend unit tests
```

• Document Workspace: (stores report components and compiled final outputs)

```
AppIO/
├── reports/
├── sec10k/
├── sec10q-q1/
├── sec10q-q2/
├── sec10q-q3/
└── user_input/
```
