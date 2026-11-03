PT Battery 4 Loader {VERSION}
README

This folder contains 8 files and 1 app. Some file names are in Japanese; the English name follows each.

  PT Battery 4 Loader.app  The app
  README (English).txt  This document
  Manual (English).html  How to use the app (opens in a browser; you can move by headings)
  Manual (English).txt  The same manual as plain text
  はじめにお読みください.txt  This document in Japanese
  マニュアル.html, マニュアル.txt  The manual in Japanese
  ライセンス.txt  License: the terms of use of this software (MIT License, in English with a Japanese summary)
  同梱部品のライセンス.txt  Third-party licenses: the terms of the parts made by others inside the app

== What it is

An app that lets you find drum samples and the like, from Splice or your own folders, listen to them,
and load them into the cells of Native Instruments Battery 4 inserted in Pro Tools.
The app performs the mouse drag on your behalf, and reads out whether the load worked.
It is designed to be used with VoiceOver and the keyboard only. "PT" in the name stands for Pro Tools.
Battery 4 is a product of Native Instruments and Pro Tools is a product of Avid Technology;
this app is not affiliated with those companies.

== Requirements

- A Mac with Apple silicon (M1 or later), macOS 14 or later (tested by the author on 15.7.8)
- Pro Tools (tested by the author on 2026.4.1)
- Native Instruments Battery 4 (tested by the author on 4.3.1)
- VoiceOver
- The app's window and speech follow your Mac's language settings: English or Japanese (chapter 2 of the manual)

== Installing, in short

1. Move "PT Battery 4 Loader.app" from this folder to the Applications folder
2. Open it. macOS may stop it from opening the first time.
   See "3.2 Opening it for the first time" in the manual
3. In System Settings, Privacy & Security, Accessibility, allow PT Battery 4 Loader,
   and open the app again (3.3 in the manual)
4. In Settings (Command+Comma), register your sample folders, and scan with Command+O
5. In Pro Tools, open the window of the target Battery 4, and only that one

For details, read "Manual (English).html" (or "Manual (English).txt").
Inside the app, Command+Shift+K opens the list of keyboard commands.

== Important

Always keep only one Battery window open. Pro Tools opens plug-in windows on top of each other
in the same place, so with overlapping windows the app cannot tell which Battery gets the sample,
and a cell on another track could be overwritten. The app stops the load when windows overlap.

== Made with AI

This software was made by the author (Takahashi) using AI (Claude by Anthropic),
under the author's direction and judgment.
Most of the program code and of the text, including the manual, was written by the AI.
The English text has been checked by tests, but has not been heard with English VoiceOver,
or tried on an English macOS or English Pro Tools, by the author.

== License

MIT License (Copyright (c) 2026 Takahashi). It is free to use, with no warranty.
See "ライセンス.txt" for the details.
