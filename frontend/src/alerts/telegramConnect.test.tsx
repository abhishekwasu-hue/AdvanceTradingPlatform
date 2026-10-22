import { describe, expect, it } from "vitest";
import cardSource from "../components/AlertChannelsCard.tsx?raw";
import settingsSource from "../pages/SettingsPage.tsx?raw";
import stepsSource from "../setup/StepsV2.tsx?raw";
import clientSource from "../api/client.ts?raw";
import rowSource from "./TelegramConnectV2.tsx?raw";
import { TELEGRAM_TEXT, telegramDone, telegramView, type TelegramConnectState } from "./telegramConnect";

/** UX2 fix 5: Telegram in one tap - the row's states, its wiring, and no bot token or chat id on the main screens. */

const state = (over: Partial<TelegramConnectState> = {}): TelegramConnectState => ({ available: true, connected: false, own_bot: false, working: false, ...over });

describe("UX2 fix 5 states", () => {
  it("connect, waiting, connected, own bot, not available", () => {
    expect(telegramView(state(), false)).toBe("connect");
    expect(telegramView(state(), true)).toBe("waiting");
    expect(telegramView(state({ connected: true }), true)).toBe("connected");
    expect(telegramView(state({ own_bot: true, available: false }), false)).toBe("own_bot");
    expect(telegramView(state({ available: false }), false)).toBe("unavailable");
  });

  it("done only once a message went through", () => {
    expect(telegramDone(null)).toBe(false);
    expect(telegramDone(state({ connected: true }))).toBe(false);
    expect(telegramDone(state({ connected: true, working: true }))).toBe(true);
    expect(telegramDone(state({ own_bot: true, working: true }))).toBe(true);
  });

  it("plain words: no token, chat id, BotFather or floor", () => {
    for (const t of Object.values(TELEGRAM_TEXT)) expect(t).not.toMatch(/token|chat id|botfather|floor|webhook|server/i);
  });
});

describe("UX2 fix 5 wiring", () => {
  it("the row: Connect Telegram opens the link inside the tap, then asks the server until connected; test and disconnect", () => {
    expect(rowSource).toContain("Connect Telegram");
    expect(rowSource).toMatch(/window\.open\("", "_blank"\)[\s\S]*await api\.telegramConnectStart\(\)[\s\S]*tab\.location\.href = made\.url/);
    expect(rowSource).toContain("tab.opener = null");
    expect(rowSource).toMatch(/setInterval\([\s\S]*load\(\)[\s\S]*next\?\.connected/);
    expect(rowSource).toContain('api.testAlertChannel("telegram")');
    expect(rowSource).toContain('api.deleteAlertChannel("telegram")');
    expect(rowSource).toContain("Yes, disconnect");
    expect(clientSource).toContain('request<TelegramConnectState>("/alerts/telegram/connect")');
  });

  it("Settings (ui_v2) shows the one-tap row first and folds the own-bot form away; the Setup wizard uses the row", () => {
    expect(settingsSource).toMatch(/\{v2 && \([\s\S]*<TelegramConnectV2 \/>[\s\S]*<AlertChannelsCard ownBotOnly=\{v2\} \/>/);
    expect(cardSource).toContain("Use your organisation&apos;s own bot");
    expect(cardSource).toMatch(/ownBotOnly && platformTelegram \?/);
    expect(stepsSource).toContain("<TelegramConnectV2 onChanged=");
    expect(stepsSource).not.toContain("Your organisation's Telegram bot is added in Settings");
  });
});
