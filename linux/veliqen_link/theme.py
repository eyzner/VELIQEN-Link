"""Shared, compact desktop surfaces using the user's light or dark appearance."""

PALETTES = {
    False: dict(bg="#f5f5f7", sidebar="#ececf0", surface="#ffffff", raised="#f0f0f4",
                text="#232327", secondary="#6d6d76", line="#dddde3", hover="#e6e6ec",
                accent="#bf314b", accent_hover="#a9233d", soft="#f5e3e8", good="#23804d",
                field="#ffffff", error="#b51e3b", inactive="#96969f"),
    True: dict(bg="#1c1c1f", sidebar="#242428", surface="#2c2c31", raised="#34343a",
               text="#f4f4f6", secondary="#a1a1ac", line="#414149", hover="#414148",
               accent="#e25068", accent_hover="#ee657c", soft="#482c36", good="#79d2a2",
               field="#242428", error="#ff8fa0", inactive="#74747f"),
}

TEMPLATE = """
window { background: @bg@; color: @text@; }
headerbar { background: @bg@; color: @text@; border: 0; border-bottom: 1px solid @line@; box-shadow: none; min-height: 42px; }
headerbar .title { font-weight: 600; font-size: 14px; }
.sidebar { background: @sidebar@; border-right: 1px solid @line@; padding: 16px 10px 12px; }
.brand { font-size: 14px; font-weight: 700; letter-spacing: 0; }
.brand-sub { font-size: 11px; color: @secondary@; }
.phone-sidebar { padding: 12px 10px 16px; }
.phone-name { font-weight: 600; font-size: 14px; }
.nav { background: transparent; border: 0; border-radius: 8px; padding: 9px 10px; margin: 2px 0; color: @secondary@; }
.nav:hover { background: @hover@; color: @text@; }
.nav:checked { background: @soft@; color: @accent@; font-weight: 600; }
.page { padding: 20px 24px; }
.heading { font-size: 23px; font-weight: 700; }
.section { font-size: 14px; font-weight: 600; }
.muted { color: @secondary@; font-size: 12px; }
.small { color: @secondary@; font-size: 11px; }
.card { background: @surface@; border: 1px solid @line@; border-radius: 13px; padding: 14px; }
.group { background: @surface@; border: 1px solid @line@; border-radius: 13px; padding: 0; }
.group-row { padding: 12px 14px; }
.group separator { background: @line@; margin: 0 14px; }
.metric { font-size: 24px; font-weight: 600; }
.accent-text { color: @accent@; }
.status { background: @surface@; padding: 10px 16px; border-top: 1px solid @line@; font-size: 12px; color: @secondary@; }
.status-error { color: @error@; }
button { background: @raised@; color: @text@; border: 1px solid transparent; border-radius: 8px; padding: 7px 12px; box-shadow: none; text-shadow: none; min-height: 20px; }
button:hover { background: @hover@; }
button:focus { outline-color: @accent@; outline-style: solid; outline-width: 1px; outline-offset: 2px; }
button:disabled { color: @inactive@; background: @raised@; }
button.accent { background: @accent@; color: white; font-weight: 600; }
button.accent:hover { background: @accent_hover@; }
button.accent:disabled { background: @raised@; color: @inactive@; }
button.danger { color: @error@; background: @soft@; }
entry, textview, textview text { background: @field@; color: @text@; caret-color: @accent@; }
entry { border: 1px solid @line@; border-radius: 8px; padding: 7px 10px; min-height: 22px; box-shadow: none; }
entry:focus { border-color: @accent@; box-shadow: none; }
combobox button { padding: 6px 9px; }
list, list row { background: transparent; color: @text@; }
list row { border-radius: 9px; margin: 2px 0; }
list row:selected { background: @soft@; }
list row:hover { background: @hover@; }
separator { background: @line@; min-height: 1px; min-width: 1px; }
scrolledwindow { border: 0; }
switch:checked, checkbutton check:checked { background: @accent@; border-color: @accent@; color: white; }
tooltip { background: @surface@; color: @text@; border: 1px solid @line@; }
.hero { background: @surface@; padding: 20px; }
.action-card { padding: 16px 10px; border-radius: 12px; background: @surface@; border: 1px solid @line@; }
.action-card:hover { background: @soft@; border-color: @soft@; }
.action-title { font-size: 12px; font-weight: 600; }
.action-sub { font-size: 11px; color: @secondary@; }
.good { color: @good@; }
.step { padding: 6px 0; }
.step-number { color: @accent@; font-size: 14px; font-weight: 600; margin-right: 6px; }
.quiet { background: transparent; border-color: transparent; padding: 6px 8px; }
.chip { background: @raised@; color: @secondary@; padding: 4px 8px; border-radius: 10px; font-size: 11px; }
expander { padding: 4px 0; }
expander > title { padding: 6px 0; color: @secondary@; }
.call-banner { background: @soft@; padding: 10px 18px; border-bottom: 1px solid @line@; }
.drop-zone { background: @surface@; border: 1px dashed @line@; border-radius: 14px; padding: 28px; }
.message-in { background: @raised@; border-radius: 14px 14px 14px 4px; padding: 10px 13px; }
.message-out { background: @accent@; color: white; border-radius: 14px 14px 4px 14px; padding: 10px 13px; }
.message-out .small { color: alpha(white,0.8); }
.composer { background: @surface@; padding: 10px; border: 1px solid @line@; border-radius: 11px; }
.conversation { background: @surface@; border: 1px solid @line@; border-radius: 12px; padding: 10px; }
.dialpad { padding: 8px; }
.dial-tool { background: transparent; border: 0; border-radius: 24px; padding: 6px; }
.dial-number { font-size: 26px; font-weight: 400; border-color: transparent; background: transparent; }
.dial-key { background: @raised@; border: 0; border-radius: 36px; padding: 7px; min-width: 54px; min-height: 54px; }
.dial-key:hover { background: @hover@; }
.dial-digit { font-size: 27px; font-weight: 400; }
.dial-letters { font-size: 9px; letter-spacing: 1px; color: @secondary@; }
.dial-call { background: #269e58; color: white; border: 0; border-radius: 24px; min-height: 30px; font-weight: 600; }
.dial-call:hover { background: #228c4f; }
.dial-call:disabled { background: @raised@; color: @inactive@; }
"""


def stylesheet(dark=False):
    css = TEMPLATE
    for name, value in PALETTES[bool(dark)].items():
        css = css.replace("@" + name + "@", value)
    return css.encode("utf-8")
