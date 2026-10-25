# 📜 Veil Utilities — Upgraded Logging Engine

## 📸 Before vs. After Comparison

### ❌ Before (Old Logs from Screenshot)
* **Visual Appearance:** Drab neutral dark gray embed (`0x2B2D31`), plain text description.
* **Actor Attribution:** Completely missing. No one knows whether Bloxlink, an admin, or an integration added/removed the roles.
* **Role List:** Clunky comma-separated list with awkward spacing: `@new role , @Bloxlink Updater , @Bloxlink Bypass`.
* **Burst Spam:** Bloxlink / Discord assigning 4 roles triggers 4 distinct embeds in `#veil-utilities-logging` within seconds of each other.
* **Thumbnails & Headers:** Blank author, no member avatar thumbnail, no member ID footer.

---

### ✨ After (Redesigned Midtown Event Logging)

#### 1. Roles Added (Clean Emerald Green `#43B581`)
```
┌─────────────────────────────────────────────────────────────┐
│ [Avatar] Roles Added — Jacob                                │
├─────────────────────────────────────────────────────────────┤
│ User                               Responsible Actor        │
│ @Jacob (ham6600y)                  🤖 @Bloxlink (Bloxlink)  │
│                                                             │
│ ➕ Added (4)                                                │
│ • @new role (new role)                                      │
│ • @Bloxlink Updater (Bloxlink Updater)                      │
│ • @Bloxlink Bypass (Bloxlink Bypass)                        │
│ • @Genre Management (Genre Management)                      │
├─────────────────────────────────────────────────────────────┤
│ User ID: 123456789 • Today at 1:41 PM                       │
└─────────────────────────────────────────────────────────────┘
```

#### 2. Roles Removed (Crimson Red `#F04747`)
```
┌─────────────────────────────────────────────────────────────┐
│ [Avatar] Roles Removed — Jacob                              │
├─────────────────────────────────────────────────────────────┤
│ User                               Responsible Actor        │
│ @Jacob (ham6600y)                  @Admin (Admin#1234)      │
│                                                             │
│ ➖ Removed (1)                                              │
│ • @Genre Operational Management                             │
│                                                             │
│ Reason: Demoted per staff team review                       │
├─────────────────────────────────────────────────────────────┤
│ User ID: 123456789 • Today at 1:42 PM                       │
└─────────────────────────────────────────────────────────────┘
```

#### 3. Mixed Changes (Discord Blurple `#5865F2`)
```
┌─────────────────────────────────────────────────────────────┐
│ [Avatar] Roles Updated — Jacob                              │
├─────────────────────────────────────────────────────────────┤
│ User                               Responsible Actor        │
│ @Jacob (ham6600y)                  @Admin (Admin#1234)      │
│                                                             │
│ ➕ Added (1)                                                │
│ • @Genre Management (Genre Management)                      │
│                                                             │
│ ➖ Removed (1)                                              │
│ • @Genre Operational Management                             │
├─────────────────────────────────────────────────────────────┤
│ User ID: 123456789 • Today at 1:43 PM                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Key Improvements Implemented

1. **Intelligent Anti-Spam Debouncer (1.2s Window)**:
   - When integrations like Bloxlink or auto-moderation tools add/remove several roles rapidly, Veil Utilities debounces the updates.
   - It captures the initial state and the final state, calculating the net added and net removed roles to emit **one single, unified, comprehensive embed** rather than flooding the channel with 4–5 individual messages.

2. **Audit Log Attribution**:
   - Automatically cross-references recent Discord audit logs (within 15 seconds) to identify the responsible actor (identifying bots like `🤖 @Bloxlink` or staff members) and display any attached reason.

3. **Dynamic Contextual Coloring**:
   - `Colors.success` (`#43B581`) when roles are added.
   - `Colors.error` (`#F04747`) when roles are removed.
   - `Colors.blurple` (`#5865F2`) when both additions and removals occur simultaneously.

4. **Structured Tag & Bullet Formatting**:
   - Displays roles in clear, readable bulleted lines with both the clickable `@mention` and raw name (`• @Role (Role Name)`).
   - Gracefully scales to inline lists if more than 8 roles change at once.

5. **Upgraded Server & Channel Suite**:
   - **Role Create / Delete / Update:** Shows color swatches, hoisted/mentionable state, granted/revoked permissions, and responsible creator/editor.
   - **Channel Create / Delete:** Shows channel name, category hierarchy, type, and responsible actor.
   - **Voice State Updates:** Color-coded for Joined (Green), Left (Red), and Moved (Blurple) with user thumbnail.
   - **Member Nickname & Timeout Updates:** Displays before/after diffs, moderator attribution, and relative timeout expiration timestamps.
