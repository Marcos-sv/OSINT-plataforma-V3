# OSINT-plataforma (prototipo da versão 3)

Sistema de busca OSINT em fontes públicas e governamentais, com suporte a consultas por:

- nome completo;
- CPF;
- nome completo + CPF.

O sistema utiliza o arquivo `providers.json` para definir as fontes consultadas.


## Autoteste

Antes de executar buscas reais, rode:

```powershell windows
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --self-test

powershell linux
./.venv-1/bin/python ./osint.py --providers-file ./providers.json --self-test
```

O autoteste verifica o funcionamento básico do programa, incluindo carregamento dos providers e filtros de nome/CPF.

## Buscar somente por nome

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --nome "NOME COMPLETO"
```

Exemplo:

```powershell
.\.venv-1\Scripts\python.exe .\osint_governo.py --providers-file .\providers_governo.json --nome "Joao da Silva"
```

## Buscar somente por CPF

O CPF pode ser informado com ou sem pontuação.

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --cpf "123.456.789-09"
```

Também funciona assim:

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --cpf "12345678909"
```

## Buscar por nome + CPF

Comando:

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --nome "NOME COMPLETO" --cpf "123.456.789-09"
```

Exemplo:

```powershell
.\.venv-1\Scripts\python.exe .\osint_governo.py --providers-file .\providers_governo.json --nome "Joao da Silva" --cpf "123.456.789-09"
```

## Saída

Por padrão, o resultado completo é salvo em:

```text
resultado.json
```


## Listar providers

Para ver todas as fontes configuradas:

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --list-providers
```

## Desativar verificação de vazamentos

Para executar somente as buscas OSINT:

```powershell
.\.venv-1\Scripts\python.exe .\osint.py --providers-file .\providers.json --nome "NOME COMPLETO" --no-leak-checks
```

Também pode ser usado com CPF ou nome + CPF.
