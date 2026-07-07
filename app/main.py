from app.boot import main

if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Bot stopped cleanly.')
