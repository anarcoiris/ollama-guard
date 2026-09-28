"""Entry point: python -m guard"""

import asyncio

from guard.run import main

if __name__ == "__main__":
    asyncio.run(main())
