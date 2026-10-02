# JaysonSellsCars live inventory: setup guide

This keeps the vehicle list on your **Test Drive** and **Contact Us** forms current without you touching any code.

**How it works**
1. Every Monday morning (about 6 AM Kelowna time), a free GitHub robot reads Valley Mitsubishi's public inventory pages.
2. It saves what it finds to a small file called `inventory.json`.
3. Your forms load that file every time someone opens them. Sold vehicles disappear, new arrivals appear, and prices stay current.

If anything goes wrong (the website changes, the robot can't reach it, GitHub is down), your forms quietly keep using the last good list. A bad run can never empty your forms.

> **About "real-time":** this is a weekly refresh, not instant. A vehicle can sell on Tuesday and still be bookable until the next run. Daily is better, and it's a one-word change (see Step 7).

---

## Step 1: Create a free GitHub account (5 minutes)

1. Go to **github.com** and click **Sign up**.
2. Choose a username and write it down. You'll need it in Step 5.

## Step 2: Create the storage folder

1. Click the **+** at the top right, then **New repository**.
2. Name it exactly: `jaysonsellscars-inventory`
3. Choose **Public**. (Required so your forms can read the file. It only contains vehicle details that are already public on Valley Mitsubishi's site.)
4. Click **Create repository**.

## Step 3: Upload the files

1. On the new empty repository page, click **uploading an existing file**.
2. Drag in these three files from the folder I gave you:
   - `scrape_inventory.py`
   - `inventory.json`
   - `README.md`
3. Click **Commit changes**.

Now add the schedule file. It lives in a hidden folder, so create it by typing:

4. Click **Add file > Create new file**.
5. In the name box type exactly: `.github/workflows/update-inventory.yml` (typing the slashes creates the folders).
6. Open `update-inventory.yml` from my folder in Notepad, copy everything, and paste it into the big box.
7. Click **Commit changes**.

## Step 4: Give the robot permission to save

1. Click **Settings** (top of the repository), then **Actions > General** on the left.
2. Scroll to **Workflow permissions** and choose **Read and write permissions**.
3. Click **Save**.

## Step 5: Point your forms at the file

1. Open `jaysonsellscars-test-drive-scheduler.html` in Notepad. Press **Ctrl+F** and search for `YOUR_GITHUB_USERNAME`.
2. Replace it with your GitHub username. The line should end up looking like this:
   `https://raw.githubusercontent.com/jaysonsmith/jaysonsellscars-inventory/main/inventory.json`
3. Save. Do the same in `jaysonsellscars-contact-form.html`.
4. In Squarespace, open each Code Block, delete the old code, paste the new file, and save.

## Step 6: Run it once and check the results (important)

1. In your repository click **Actions**, then **Update inventory** on the left, then **Run workflow** (green button), then **Run workflow** again.
2. Wait 3 to 6 minutes. A **green check** means success. A red X means it needs attention (see below).
3. Open the file **`inventory-report.txt`** in your repository. It lists how many new and used vehicles it found and how many per model.
4. **Compare those numbers with valleymitsubishi.ca.** They should match. If the counts look wrong, tell me.
5. Check your forms: search a recent stock number and confirm it shows up, and that a vehicle you know has sold does not.

**Check the first run carefully.** I built and tested the robot on a stand-in copy of Valley Mitsubishi's pages, because I can't see the real website's code from my side. Step 6 is how we confirm it reads the real pages correctly.

## Step 7: Change how often it runs (optional)

Open `.github/workflows/update-inventory.yml` in GitHub, click the pencil, and change this line:

- Weekly on Monday (current): `cron: '0 13 * * 1'`
- **Every day (recommended):** `cron: '0 13 * * *'`

Then click **Commit changes**. You can also press **Run workflow** any time, for example right after a big delivery day.

---

## If something goes wrong

- **Red X on a run:** GitHub also emails you. Click the failed run, click **update**, then the step with the red X, and send me the last 30 lines. Nothing is lost: your forms keep the previous list.
- **"found only N vehicles... Refusing to overwrite":** the website probably changed. The safety check stopped it from replacing your good list. Send me the log and I'll adjust the robot.
- **The forms still show the old list:** check that Step 5 was saved and re-pasted into Squarespace, and that opening the address from Step 5 in a browser shows a page full of vehicle data.
- **Stop it:** in the repository go to **Actions > Update inventory > ... > Disable workflow**.

## Good to know

- **Be gentle with the website.** The robot waits one second between pages and runs once a week. Please don't schedule it more often than daily. It only reads pages anyone can open.
- **A more reliable option:** Valley Mitsubishi's website provider almost certainly offers an official inventory feed (the same one used for AutoTrader or CarGurus). If you can get that link from your manager, I can switch the robot to it, and it will not break when the website changes.
- **Mark a vehicle sold right away:** you don't need to wait for the next run. Add it to `unitOverrides` in the form code, for example `'26165-NEW': { status: 'sold' }`. This is only needed for same-day changes.
- **GitHub pauses schedules after 60 days with no changes.** That won't happen while inventory changes every week.
