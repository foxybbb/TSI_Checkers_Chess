# -*- coding: utf-8 -*-
"""
Created on Wed May 26 18:46:13 2021

@author: Zach
"""

import tkinter as tk
from tkinter import ttk
from random import randrange


def mainMenu():
    """Initial menu for choosing settings in the chess game."""
    WINDOW_TITLE = "Chess Menu"
    WINDOW_BG = "#5285b4"
    TEXT_COLOR = "#f5f5f5"
    SCREEN_SIZE = "500x200"
    TEXT_KWARGS = dict(
        bg = WINDOW_BG,
        fg = TEXT_COLOR,
    )

    root = tk.Tk()
    root.geometry(SCREEN_SIZE)
    root.title(WINDOW_TITLE)
    root.configure(bg=WINDOW_BG)
    tk.Label(
        root, text="Chess", font="Helvetica 20 bold", **TEXT_KWARGS).pack()

    # Start game button
    def getValues():
        global humanWhite, humanBlack
        
        # Игра всегда против компьютера, игрок всегда за белых
        humanWhite, humanBlack = True, False

        root.destroy()

    tk.Button(
        root,
        text="Play Chess!",
        font="Helvetica 15 bold",
        fg="#222",
        bg="#EEE",
        padx=2,
        command=getValues,
    ).place(x=190, y=100)

    root.mainloop()
    return (humanWhite, humanBlack)
