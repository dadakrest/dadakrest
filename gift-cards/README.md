# 🎁 Family Gift Cards

A small phone app for the family. Kids earn **Peace Points** for getting along (sharing, saying sorry, making peace with a sibling, chores) and trade them for gift cards that parents create.

- 👧 Kid profiles for ages **1–15** (older ages are rejected)
- 🎁 Parents make gift cards: name, picture, color, cost, how many, and which ages can get them
- 🕊️ Peace Board: a parent approves each award with the PIN; "we made peace" gives both kids +10
- 👛 Each kid has a wallet with a unique code for every gift card; parents mark cards as used
- ⚙️ You can change the app name, family name, colors, what the points are called, and the Peace Board actions
- 🔄 **Syncs between phones automatically**: points, cards and settings show up on every phone within about 10 seconds while the app is open
- 📱 Installs to the home screen and works offline. The app is about 85 KB; each phone's copy of the data stays well under 1 GB

## Put it on your phone
1. Host this folder (for example, GitHub Pages: repo **Settings → Pages → Deploy from branch**, then open `/gift-cards/`).
2. Open the link on the phone.
   - **iPhone:** Safari → Share → *Add to Home Screen*
   - **Android:** Chrome → ⋮ → *Install app*
3. Tap **Parent**, create a PIN, add your kids and gift cards.

## Sync your phones
1. On the first phone: **Setup → Start syncing from this phone**. The app shows a 25-character family code.
2. On each other phone: open the app, **Setup**, type the code and tap **Join family**. Or tap **Send link** on the first phone and open the link on the other one.
3. That's it. Changes made with no internet are saved and sent when the phone is back online. If two phones spend the same points at the same moment, only one gets the card, and the other phone says so.

Keep the family code private: anyone who has it can see and change your family's data. If it gets out, use **Setup → Add another phone → Make a new family code**. The old code then stops working and its data is deleted from the server, and your other phones join again with the new code.

## Where the data lives
Each phone keeps its own copy. To sync, changes also go to a small database on [Supabase](https://supabase.com) (project `family-gift-cards`, free plan). The database only accepts the app's five functions in [`supabase/schema.sql`](supabase/schema.sql), and only with a valid family code. The parent PIN is stored as a salted hash, never as the PIN itself.

**Free plan note:** Supabase pauses free projects after about a week without use. While it's paused the app keeps working on each phone, but phones don't sync. To wake it, open the [Supabase dashboard](https://supabase.com/dashboard/project/seqeuadetaioxuzmcbal) and press **Restore**. Phones catch up on their own once it's back.
