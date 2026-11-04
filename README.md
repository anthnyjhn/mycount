# 📒 MyCount: a simple business tracker

A very simple website you run on your own computer to keep track of:

- 💰 **Sales (jobs)**: job number, job name, customer, date, quoted price, expenses, profit with margin %, job status (new job / pending / done), paid / not yet
- 🛒 **Purchases**: what you bought (supplier, product, amount, paid / unpaid, date)
- 🔩 **Parts**: a list of things you've bought before and where to buy them again (supplier, item name, description, store link)
- 🧑 **Customers**: name (required), address, phone, email
- 🏭 **Suppliers**: name (required), address, store link
- 📊 **Dashboard**: revenue, expenses, profit, average profit margin, this month vs last month, revenue and profit goals with progress, and a monthly breakdown (income, expenses, number of jobs)
- ⚙️ **Settings**: business name, currency, financial year, and **export all your data to CSV**

---

## 🚀 How to run it (step by step)

### Step 1: Install Docker (one time only)

Download and install **Docker Desktop**:

- Windows / Mac: https://www.docker.com/products/docker-desktop/
- Linux: https://docs.docker.com/engine/install/

After installing, **open Docker Desktop** and wait until it says it is running (whale icon 🐳 is steady).

### Step 2: Get this project on your computer

Either:

- **Easy way:** on GitHub click the green **Code** button → **Download ZIP**, then unzip it.
- **Git way:** `git clone https://github.com/anthnyjhn/mycount.git`

### Step 3: Open a terminal in the project folder

- **Windows:** open the `mycount` folder, click the address bar, type `cmd`, press Enter.
- **Mac:** right-click the `mycount` folder → **New Terminal at Folder**.
- **Linux:** open a terminal and `cd` into the folder.

### Step 4: Start it

Type this and press Enter:

```bash
docker compose up -d --build
```

The first time takes a minute or two (it downloads what it needs).

### Step 5: Open it

Open your web browser and go to: **http://localhost:8000** 🎉

Start on the **⚙️ Settings** page to set your business name, money sign and the month your financial year starts.

---

## 🧰 Everyday commands

Run these in the project folder:

| What you want | Command |
| --- | --- |
| Start it | `docker compose up -d` |
| Stop it | `docker compose down` |
| See if it is running | `docker compose ps` |
| See error messages | `docker compose logs -f` (press Ctrl+C to exit) |
| Update after changing code | `docker compose up -d --build` |

It starts again by itself when your computer restarts (as long as Docker Desktop is running).

---

## 💾 Where is my data? (and backups)

All your data is in one file: **`data/mycount.db`** inside the project folder.

- **To back up:** stop the app (`docker compose down`), copy `data/mycount.db` somewhere safe (USB, Google Drive...), start it again.
- **To restore:** stop the app, put your backup copy back at `data/mycount.db`, start it again.

⚠️ Don't delete the `data` folder, because that deletes all your records.

---

## ⬆️ Updating to a new version

1. Get the new code (`git pull`, or download the ZIP again and copy your `data` folder into it).
2. Run `docker compose up -d --build`.

Your data is kept. When an update changes how data is stored, the app first saves a copy of your database next to it (for example `data/mycount.db.before-jobs-update.bak`).

---

## 📱 Use it on your phone (same Wi-Fi)

1. Find your computer's local IP address (Windows: `ipconfig`, Mac: System Settings → Wi-Fi → Details). It looks like `192.168.1.25`.
2. On your phone, open `http://192.168.1.25:8000`.

There is **no password** on this app, so only use it on your own home/office network. Don't put it on the open internet.

---

## ❓ Problems?

- **"port is already allocated"**: something else uses port 8000. Open `docker-compose.yml`, change `"8000:8000"` to `"8080:8000"`, run `docker compose up -d`, then open http://localhost:8080.
- **"Cannot connect to the Docker daemon"**: Docker Desktop is not open. Open it and wait until it is running.
- **Page doesn't load**: run `docker compose logs -f` to see what's wrong.

---

## 🧮 How the numbers work

- **Profit** on a job = quoted price − expenses (calculated for you). The **margin** in brackets = profit ÷ quoted price.
- **Income** = quoted price of all jobs in the selected financial year (paid *and* not yet paid).
- **Expenses** = all purchases + all job expenses in the selected financial year.
- **Profit / Loss** = Income − Expenses. **Net margin** = Profit ÷ Income.
- **Avg. profit margin** = (all quoted prices − all job expenses) ÷ all quoted prices for the year.
- **Goals**: set a revenue and/or profit goal for each financial year on the dashboard. The black marker on each bar shows where you should be by today. *On track* means you're at or ahead of it.
- **Job status** (New job → Pending → Done) shows how far along a job is. It doesn't change any money numbers: a job counts as income from its date, whatever its status.
- **Customers still owe me** = jobs marked *Not yet*. Tap the ⏳ button on a job to mark it ✅ Paid.
- **Bills still to pay** = purchases marked *unpaid*. Tap the ⏳ Unpaid button on a purchase to mark it ✅ Paid.

💡 A cost you typed as a job's **Expenses** should not also be added on the Purchases page, or it is counted twice.

## 📤 Exporting your data

Go to **Settings → Export data**. Download each list as a `.csv` file, or click **Download all (.zip)** to get every list at once. CSV files open in Excel, Google Sheets and Numbers.

## 🛠️ For developers (without Docker)

```bash
pip install -r requirements.txt
python app.py        # http://localhost:8000
```

Built with Python (Flask) + SQLite. No JavaScript build step.
