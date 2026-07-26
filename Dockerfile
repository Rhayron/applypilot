FROM python:3.12-slim

# deps de sistema: WeasyPrint (PDF) + Playwright (Chromium) + LibreOffice.
# O LibreOffice é o que converte o .docx adaptado em PDF preservando a formatação
# do currículo original; só o -writer, não a suíte inteira.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    libffi-dev shared-mime-info fonts-dejavu \
    libreoffice-writer libreoffice-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Camada 1: só o manifesto + um pacote vazio, para que dependências e o download do
# Chromium (~114 MB) fiquem em cache e não sejam refeitos a cada mudança de código.
COPY pyproject.toml README.md ./
RUN mkdir -p autoapply && touch autoapply/__init__.py \
    && pip install --no-cache-dir ".[pdf,apply]" \
    && playwright install --with-deps chromium

# Camada 2: o código de verdade — barata de reconstruir.
COPY autoapply ./autoapply
RUN pip install --no-cache-dir --no-deps --force-reinstall .

# monte seu diretório de dados (config.yaml, profile/, out/, autoapply.db) em /data
WORKDIR /data
ENTRYPOINT ["autoapply", "-c", "/data/config.yaml"]
CMD ["run"]
