from definitions.guy import GUI_Main
if __name__ == '__main__':
    app = GUI_Main()
    app.root.protocol("WM_DELETE_WINDOW", app.on_closing)

    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Shutting down...")
        app.on_closing()
