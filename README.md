OSINT-plataforma — Versão 3

Plataforma OSINT com interface web para realizar consultas em fontes públicas e executar buscas com o Maigret.

O ponto de entrada atual do programa é o arquivo app.py.

Importante: não é necessário executar osint.py ou o Maigret manualmente para utilizar a interface. O app.py chama esses componentes internamente.

Estrutura principal

Os arquivos e diretórios mais importantes para executar o sistema são:

OSINT-plataforma-V3/
├── app.py
├── osint.py
├── providers.json
├── requirements.txt
├── maigret/
├── templates/
├── static/
├── relatorios/
└── uploads/

app.py: inicia a aplicação Flask e a interface web.

osint.py: mecanismo principal das consultas OSINT.

providers.json: configuração das fontes utilizadas pelo OSINT.

maigret/maigret: instalação local do Maigret utilizada pela aplicação.

relatorios/: relatórios JSON gerados pelo sistema.

uploads/: arquivos enviados pela interface, incluindo imagens destinadas ao fluxo do FaceCheck.

Instalação no Linux

1. Acessar a pasta do projeto

Abra um terminal e entre na pasta raiz do projeto:

cd /home/marcosvsv/projetos/OSINT/OSINT-plataforma-V3

Caso o projeto esteja em outro local, ajuste o caminho.

Para confirmar que está na pasta correta:

ls

Devem aparecer arquivos como:

app.py
osint.py
providers.json
requirements.txt
maigret

2. Criar um ambiente virtual

Para evitar conflito com ambientes virtuais antigos, crie um ambiente próprio para a execução atual:

python3 -m venv .venv-run

Ative o ambiente:

source .venv-run/bin/activate

Após a ativação, o terminal normalmente passa a mostrar algo semelhante a:

(.venv-run) usuario@computador:~/projetos/OSINT/OSINT-plataforma-V3$

Caso o comando venv não esteja disponível

No Ubuntu/Debian, instale o pacote necessário:

sudo apt install python3-venv

Depois crie e ative novamente o ambiente:

python3 -m venv .venv-run
source .venv-run/bin/activate

3. Atualizar o pip

Com o ambiente virtual ativado:

python -m pip install --upgrade pip

4. Instalar as dependências

Ainda dentro da pasta raiz do projeto:

pip install -r requirements.txt

O arquivo requirements.txt instala atualmente:

ddgs
requests
beautifulsoup4
Flask
Maigret

O Maigret é instalado diretamente da cópia existente no projeto:

./maigret/maigret

Por isso, execute pip install -r requirements.txt estando dentro da pasta OSINT-plataforma-V3.

5. Verificar as dependências

Opcionalmente, confirme se os principais módulos foram instalados corretamente:

python -c "import flask, requests, bs4, ddgs, maigret; print('Dependencias OK')"

Se estiver tudo correto, será exibido:

Dependencias OK

Executando o programa

Com o ambiente virtual ativado e estando na raiz do projeto, execute:

python app.py

A aplicação Flask será iniciada em:

http://127.0.0.1:5000

Abra esse endereço no navegador.

O terminal deverá mostrar algo semelhante a:

* Serving Flask app 'app'
* Debug mode: on
* Running on http://127.0.0.1:5000

Processo para as próximas execuções

Depois que o ambiente virtual e as dependências já estiverem instalados, não é necessário repetir a instalação.

Use apenas:

cd /home/marcosvsv/projetos/OSINT/OSINT-plataforma-V3
source .venv-run/bin/activate
python app.py

Depois acesse:

http://127.0.0.1:5000

Encerrando o programa

Para parar o servidor Flask:

Ctrl + C

Para sair do ambiente virtual:

deactivate

Como o sistema executa as consultas

OSINT

Quando uma consulta por nome e/ou CPF é realizada pela interface, o app.py executa automaticamente o osint.py usando o mesmo interpretador Python que iniciou o servidor.

O arquivo utilizado para configurar as fontes é:

providers.json

Portanto, para o uso normal pela interface, não é necessário executar comandos do osint.py manualmente.

Maigret

As buscas por username também são iniciadas pela interface.

O app.py utiliza a instalação local localizada em:

maigret/maigret

O Maigret também não precisa ser iniciado separadamente.

FaceCheck

A aplicação já possui a etapa de recebimento de imagens para o fluxo do FaceCheck.

As imagens aceitas são:

JPG
JPEG
PNG
WEBP

O tamanho máximo configurado para upload é de 10 MB.

As imagens originais são armazenadas em:

uploads/facecheck/original/

A integração completa com o sistema de melhoria de imagem e com o FaceCheck depende das próximas etapas do projeto.

Relatórios

Os relatórios gerados pelo OSINT e pelo Maigret são armazenados em:

relatorios/

Exemplos:

osint_20260923_103000.json
maigret_usuario_20260923_103500.json
resultado_integrado.json

Quando existe um relatório OSINT e um relatório Maigret disponíveis na mesma execução do servidor, o sistema também pode gerar:

resultado_integrado.json

Resumo rápido

Para executar normalmente:

cd /home/marcosvsv/projetos/OSINT/OSINT-plataforma-V3
source .venv-run/bin/activate
python app.py

Acesse:

http://127.0.0.1:5000

O fluxo principal é:

Navegador
   ↓
app.py (Flask)
   ├── OSINT → osint.py → providers.json
   ├── Maigret → maigret/maigret
   └── FaceCheck → upload/preparação da imagem
            ↓
       relatorios/