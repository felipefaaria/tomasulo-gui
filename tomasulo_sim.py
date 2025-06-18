import collections
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import os # Importar o módulo os para verificar a existência do arquivo

# Constantes globais para estados e tipos de branch
JUMP = "JUMP"
PREDICT_NOT_TAKEN = "NOT_TAKEN"
PREDICT_TAKEN = "TAKEN"

# --- Classe Instruction (Instrução) ---
# Representa uma única instrução MIPS-like e seus atributos.
class Instruction:
    def __init__(self, op, rs1, rs2=None, rd=None, shamt=None, imn=None):
        self.opname = op # Nome da operação (ex: 'ADD', 'LW', 'BEQ')
        self.destination = rd # Registrador de destino (Rd)
        self.source1 = rs1 # Primeiro operando fonte (Rs1 - base para mem, ou primeiro operando)
        self.source2 = rs2 # Segundo operando fonte (Rs2 - valor a armazenar para SW, ou segundo operando)
        self.immediate = shamt # Valor imediato (para SLLI, SRLI)
        self.address = imn # Endereço/offset (para LW, SW, BEQ, BNE)
        
        # Ciclos restantes para a execução na Unidade Funcional (UF)
        self.execution_cycles_remaining = self._get_execution_cycles(op)
        self.ready_to_write = False # True quando a execução na UF é concluída
        
        # Ciclos de clock em que a instrução entra/sai de cada estágio
        self.issue_cycle = -1
        self.execute_start_cycle = -1
        self.write_result_cycle = -1
        self.commit_cycle = -1

    # Define a duração da execução de cada tipo de instrução em ciclos
    def _get_execution_cycles(self, opname):
        if opname in ['ADD', 'SUB']:
            return 2 # Operações aritméticas
        elif opname in ['SLLI', 'SRLI', 'OR', 'AND', 'BEQ', 'BNE']:
            return 1 # Operações lógicas/shift/branch (assumido 1 ciclo)
        elif opname in ['LW', 'LB', 'SW', 'SB']:
            return 3 # Acesso à memória
        else:
            return 1 # Padrão para outras operações não especificadas

    # Representação em string da instrução
    def __str__(self):
        if self.opname in ['SLLI', 'SRLI']:
            return f'{self.opname} {self.destination}, {self.source1}, {self.immediate}'
        elif self.opname in ['LW', 'LB']:
            return f'{self.opname} {self.destination}, {self.source1}, {self.address}'
        elif self.opname in ['SW', 'SB']:
            return f'{self.opname} {self.source2}, {self.source1}, {self.address}'
        elif self.opname in ['BEQ', 'BNE']:
            return f'{self.opname} {self.source1}, {self.source2}, {self.address}'
        else: # R-type (ADD, SUB, OR, AND)
            return f'{self.opname} {self.destination}, {self.source1}, {self.source2}'

# --- Classe Register (Registrador) ---
# Representa um registrador físico no arquivo de registradores.
class Register:
    def __init__(self, name):
        self.name = name # Nome do registrador (ex: 'R0')
        self.value = 0 # Valor atual
        self.reorder_tag = None # ID da entrada do ROB que produzirá o próximo valor (para renomeação)
        self.busy = False # True se o registrador está esperando um valor de uma instrução fora de ordem

    # Limpa o estado de "ocupado" do registrador
    def clear(self):
        self.reorder_tag = None
        self.busy = False

    def __str__(self):
        return f'{self.name}: Val={self.value}, ROB={self.reorder_tag}, Busy={self.busy}'

# --- Classe ReorderBufferPos (Posição do Buffer de Reordenação) ---
# Representa uma entrada no Reorder Buffer (ROB).
class ReorderBufferPos:
    def __init__(self, id, instruction, destination_reg, inst_type):
        self.id = id # ID único da entrada do ROB
        self.busy = False # Inicialmente Não Ocupado; True quando uma instrução é emitida
        self.instruction = instruction # Objeto Instruction associado
        self.state = "" # Estado da instrução no ROB ("Issued", "Executing", etc.)
        self.destination_reg = destination_reg # Registrador de destino ou endereço de memória (string)
        self.value = None # Valor do resultado após a execução
        
        self.inst_type = inst_type # Tipo da instrução ("ALU", "LOAD", "STORE", "BRANCH")

        # Campos para especulação de desvio
        self.is_branch = (inst_type == "BRANCH")
        self.predicted_taken = None # Previsão (TAKEN/NOT_TAKEN)
        self.actual_taken = None # Resultado real (TAKEN/NOT_TAKEN)
        self.target_address = None # Endereço alvo do desvio

    # Limpa a entrada do ROB, tornando-a disponível
    def clear(self):
        self.busy = False
        self.instruction = None
        self.state = ""
        self.destination_reg = ""
        self.value = None
        self.inst_type = ""
        self.is_branch = False
        self.predicted_taken = None
        self.actual_taken = None
        self.target_address = None

    def __str__(self):
        return (f'#{self.id} Busy:{self.busy} Inst:{self.instruction} State:{self.state} '
                f'Dest:{self.destination_reg} Val:{self.value} Type:{self.inst_type}')

# --- Classe ReservationStation (Estação de Reserva) ---
# Representa uma entrada em uma Estação de Reserva.
class ReservationStation:
    def __init__(self, name):
        self.name = name # Nome da RS (ex: "ADD1", "MEM1")
        self.busy = False # True se a RS está ocupada por uma instrução
        self.op = None # Operação da instrução
        self.Vj = None # Valor do operando 1 (se disponível)
        self.Vk = None # Valor do operando 2 (se disponível)
        self.Qj = None # ID do ROB que produzirá o operando 1 (se não disponível)
        self.Qk = None # ID do ROB que produzirá o operando 2 (se não disponível)
        self.destination_rob_id = None # ID do ROB para onde o resultado será escrito
        self.instruction_obj = None # Referência ao objeto Instruction

    # Limpa a RS, tornando-a disponível
    def clear(self):
        self.busy = False
        self.op = None
        self.Vj = None
        self.Vk = None
        self.Qj = None
        self.Qk = None
        self.destination_rob_id = None
        self.instruction_obj = None

    # Verifica se a RS está livre
    def is_clear(self):
        return not self.busy

    def __str__(self):
        return (f'Name:{self.name} Busy:{self.busy} Op:{self.op} Vj:{self.Vj} Vk:{self.Vk} '
                f'Qj:{self.Qj} Qk:{self.Qk} Dest_ROB:{self.destination_rob_id}')

# --- Classe TomasuloSimulator (Simulador Tomasulo) ---
# Orquestra toda a simulação do algoritmo de Tomasulo.
class TomasuloSimulator:
    def __init__(self, num_mem_rs=2, num_add_rs=3, num_logic_rs=2, rob_size=8):
        self.register_file = {} # Dicionário: nome_reg -> objeto Register
        self.memory = collections.defaultdict(int) # Modelo de memória simples (endereço -> valor)
        self.instruction_queue = collections.deque() # Fila de instruções a serem emitidas (não utilizada diretamente, mas pode ser para uma fila de entrada)
        self.program_counter = 0 # Contador de Programa: índice da próxima instrução a ser emitida

        self.reservation_stations = []
        self._create_reservation_stations(num_mem_rs, num_add_rs, num_logic_rs)

        self.reorder_buffer = [ReorderBufferPos(i, None, None, None) for i in range(rob_size)] # ROB
        self.rob_head = 0 # Ponteiro para a instrução mais antiga no ROB (pronta para confirmar)
        self.rob_tail = 0 # Ponteiro para a próxima entrada livre no ROB (para emissão)

        self.current_cycle = 0 # Ciclo de clock atual da simulação
        self.committed_instructions_count = 0 # Total de instruções confirmadas
        self.bubble_cycles = 0 # Ciclos sem emissão ou confirmação (stalls)

        self.is_running = False # Flag para execução contínua
        self.step_mode = False # Flag para execução passo a passo

        self.program_instructions = [] # Lista de todos os objetos Instruction do programa

    # Cria as Estações de Reserva (RS) para diferentes UFs
    def _create_reservation_stations(self, num_mem, num_add, num_logic):
        for i in range(num_mem):
            self.reservation_stations.append(ReservationStation(f"MEM{i+1}")) # UFs de memória
        for i in range(num_add):
            self.reservation_stations.append(ReservationStation(f"ATM{i+1}")) # UFs aritméticas
        for i in range(num_logic):
            self.reservation_stations.append(ReservationStation(f"LOG{i+1}")) # UFs lógicas/shift/branch

    # Carrega instruções de um arquivo e inicializa registradores
    def load_instructions(self, filename="instructions.txt"):
        self.instruction_queue.clear()
        self.program_instructions.clear()
        self.register_file.clear() # Limpa registradores existentes ao carregar novo programa
        self.memory = collections.defaultdict(int) # Limpa a memória

        try:
            with open(filename, 'r') as f:
                for line in f.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    tokens = [t.strip(',') for t in line.split()]
                    opname = tokens[0]

                    instruction = None
                    destination = None
                    source1 = None
                    source2 = None
                    immediate = None
                    address = None
                    inst_type = "ALU"

                    if opname in ['SLLI', 'SRLI']:
                        destination = tokens[1]
                        source1 = tokens[2]
                        immediate = int(tokens[3])
                    elif opname in ['LW', 'LB']: 
                        destination = tokens[1]
                        source1 = tokens[2]
                        address = int(tokens[3])
                        inst_type = "LOAD"
                    elif opname in ['SW', 'SB']: 
                        source2 = tokens[1] # Registrador com o valor a ser armazenado
                        source1 = tokens[2] # Registrador base
                        address = int(tokens[3]) # Offset
                        inst_type = "STORE"
                    elif opname in ['BEQ', 'BNE']: 
                        source1 = tokens[1]
                        source2 = tokens[2]
                        address = int(tokens[3])
                        inst_type = "BRANCH"
                    elif opname in ['ADD', 'SUB', 'OR', 'AND']: 
                        destination = tokens[1]
                        source1 = tokens[2]
                        source2 = tokens[3]
                    else:
                        print(f"Warning: Instrução '{opname}' não reconhecida na linha: {line}. Ignorando.")
                        continue

                    instruction = Instruction(opname, source1, source2, destination, immediate, address)
                    self.program_instructions.append(instruction)

                    # Garante que todos os registradores usados sejam inicializados no register_file
                    regs_to_check = []
                    if destination: regs_to_check.append(destination)
                    if source1: regs_to_check.append(source1)
                    if source2: regs_to_check.append(source2)

                    for reg_name in regs_to_check:
                        if reg_name and reg_name not in self.register_file:
                            self.register_file[reg_name] = Register(reg_name)
            print(f"Carregadas {len(self.program_instructions)} instruções do arquivo '{filename}'.")
        except FileNotFoundError:
            print(f"Erro: Arquivo de instruções '{filename}' não encontrado.")
            messagebox.showerror("Erro de Carregamento", f"O arquivo de instruções '{filename}' não foi encontrado.")
            return False # Falha ao carregar
        return True # Sucesso ao carregar

    # Encontra uma entrada livre no ROB
    def _get_free_rob_entry(self):
        # ROB está cheio se a posição atual da cauda estiver ocupada
        if self.reorder_buffer[self.rob_tail].busy:
            return -1 
        return self.rob_tail 

    # Encontra uma RS livre e adequada para a operação da instrução
    def _get_free_rs(self, inst_opname):
        for rs in self.reservation_stations:
            if rs.is_clear():
                if inst_opname in ['LW', 'LB', 'SW', 'SB'] and rs.name.startswith("MEM"):
                    return rs
                elif inst_opname in ['ADD', 'SUB'] and rs.name.startswith("ATM"):
                    return rs
                elif inst_opname in ['SLLI', 'SRLI', 'OR', 'AND', 'BEQ', 'BNE'] and rs.name.startswith("LOG"):
                    return rs
        return None

    # Estágio de Emissão (Issue)
    def issue_stage(self):
        issued_this_cycle = False
        if self.program_counter < len(self.program_instructions):
            inst_to_issue = self.program_instructions[self.program_counter]
            
            rob_id = self._get_free_rob_entry()
            rs_entry = self._get_free_rs(inst_to_issue.opname)

            if rob_id != -1 and rs_entry is not None:
                # 1. Aloca entrada no ROB
                rob_pos = self.reorder_buffer[rob_id]
                rob_pos.busy = True 
                rob_pos.instruction = inst_to_issue
                rob_pos.state = "Issued"
                
                # Define o destino no ROB (registrador ou endereço de memória)
                if inst_to_issue.destination: # ALU, LOAD
                    rob_pos.destination_reg = inst_to_issue.destination
                elif inst_to_issue.opname in ['SW', 'SB']: # STORE
                    base_reg_val = self.register_file[inst_to_issue.source1].value if inst_to_issue.source1 in self.register_file else 0
                    rob_pos.destination_reg = f"Mem[{inst_to_issue.address} + {inst_to_issue.source1} (Val:{base_reg_val})]"
                else: # Branch
                    rob_pos.destination_reg = None

                rob_pos.target_address = inst_to_issue.address

                # Define o tipo da instrução no ROB
                if inst_to_issue.opname in ['ADD', 'SUB', 'SLLI', 'SRLI', 'OR', 'AND']:
                    rob_pos.inst_type = "ALU"
                elif inst_to_issue.opname in ['LW', 'LB']:
                    rob_pos.inst_type = "LOAD"
                elif inst_to_issue.opname in ['SW', 'SB']:
                    rob_pos.inst_type = "STORE"
                elif inst_to_issue.opname in ['BEQ', 'BNE']:
                    rob_pos.inst_type = "BRANCH"
                    rob_pos.predicted_taken = PREDICT_NOT_TAKEN # Previsão: não tomado
                else:
                    rob_pos.inst_type = "UNKNOWN"

                inst_to_issue.issue_cycle = self.current_cycle # Registra ciclo de emissão

                # 2. Aloca e configura a entrada na RS
                rs_entry.busy = True
                rs_entry.op = inst_to_issue.opname
                rs_entry.destination_rob_id = rob_id
                rs_entry.instruction_obj = inst_to_issue

                # Trata os operandos (Vj, Vk, Qj, Qk) para a RS
                # Operando 1 (Vj/Qj): Reg. base para mem, ou 1o operando para ALU/Branch
                if inst_to_issue.source1:
                    reg1 = self.register_file[inst_to_issue.source1]
                    if reg1.busy and reg1.reorder_tag is not None:
                        rob_entry_src1 = self.reorder_buffer[reg1.reorder_tag]
                        if rob_entry_src1.state == "Write Result" and rob_entry_src1.value is not None:
                            rs_entry.Vj = rob_entry_src1.value # Valor pronto
                        else:
                            rs_entry.Qj = reg1.reorder_tag # Valor pendente do ROB
                    else:
                        rs_entry.Vj = reg1.value # Valor direto do registrador
                
                # Operando 2 (Vk/Qk): Diferente para cada tipo de instrução
                if inst_to_issue.opname in ['SLLI', 'SRLI']:
                    rs_entry.Vk = inst_to_issue.immediate # Valor imediato
                elif inst_to_issue.opname in ['SW', 'SB']:
                    # Para SW/SB: source2 é o registrador que contém o VALOR a ser armazenado
                    if inst_to_issue.source2:
                        reg2 = self.register_file[inst_to_issue.source2]
                        if reg2.busy and reg2.reorder_tag is not None:
                            rob_entry_src2 = self.reorder_buffer[reg2.reorder_tag]
                            if rob_entry_src2.state == "Write Result" and rob_entry_src2.value is not None:
                                rs_entry.Vk = rob_entry_src2.value
                            else:
                                rs_entry.Qk = reg2.reorder_tag
                        else:
                            rs_entry.Vk = reg2.value
                elif inst_to_issue.source2: # Para ADD/SUB/OR/AND/BEQ/BNE/LW/LB (2o operando de registrador)
                    # Note: LW/LB apenas usam source1 (base) + address (offset), não um segundo registrador em Vk
                    # O código abaixo é genérico para qualquer instrução com source2 como registrador
                    reg2 = self.register_file[inst_to_issue.source2]
                    if reg2.busy and reg2.reorder_tag is not None:
                        rob_entry_src2 = self.reorder_buffer[reg2.reorder_tag]
                        if rob_entry_src2.state == "Write Result" and rob_entry_src2.value is not None:
                            rs_entry.Vk = rob_entry_src2.value
                        else:
                            rs_entry.Qk = reg2.reorder_tag
                    else:
                        rs_entry.Vk = reg2.value

                # 3. Atualiza o Register File para renomeação de destino
                if inst_to_issue.destination and inst_to_issue.opname not in ['SW', 'SB', 'BEQ', 'BNE']:
                    dest_reg = self.register_file[inst_to_issue.destination]
                    dest_reg.busy = True # Marca registrador como "ocupado"
                    dest_reg.reorder_tag = rob_id # Renomeia para o ID do ROB

                # 4. Avança o Program Counter e a cauda do ROB
                self.program_counter += 1 # PC avança para próxima instrução a ser buscada
                self.rob_tail = (self.rob_tail + 1) % len(self.reorder_buffer)
                issued_this_cycle = True
        return issued_this_cycle

    # Estágio de Execução (Execute)
    def execute_stage(self):
        for rs in self.reservation_stations:
            # Instrução pode executar se RS está ocupada e todos os operandos (Vj, Vk) estão disponíveis (Qj, Qk são None)
            if rs.busy and rs.Qj is None and rs.Qk is None:
                inst_obj = rs.instruction_obj
                rob_entry = self.reorder_buffer[rs.destination_rob_id]
                
                if inst_obj.execute_start_cycle == -1: # Primeira vez no estágio de execução
                    inst_obj.execute_start_cycle = self.current_cycle
                    rob_entry.state = "Executing"
                
                inst_obj.execution_cycles_remaining -= 1 # Decrementa ciclos restantes

                if inst_obj.execution_cycles_remaining == 0:
                    # Execução concluída
                    inst_obj.ready_to_write = True
                    rob_entry.state = "Ready to Write"

                    result = None
                    if inst_obj.opname in ['ADD', 'SUB', 'OR', 'AND']:
                        val1 = rs.Vj if rs.Vj is not None else 0
                        val2 = rs.Vk if rs.Vk is not None else 0
                        if inst_obj.opname == 'ADD': result = val1 + val2
                        elif inst_obj.opname == 'SUB': result = val1 - val2
                        elif inst_obj.opname == 'OR': result = val1 | val2
                        elif inst_obj.opname == 'AND': result = val1 & val2
                    elif inst_obj.opname in ['SLLI', 'SRLI']:
                        val = rs.Vj if rs.Vj is not None else 0
                        shift_amount = rs.Vk if rs.Vk is not None else 0 
                        if inst_obj.opname == 'SLLI': result = val << shift_amount
                        elif inst_obj.opname == 'SRLI': result = val >> shift_amount
                    elif inst_obj.opname in ['LW', 'LB']:
                        # Calcula endereço efetivo: Base (Vj) + Offset (Vk/address da instrução)
                        base_val = rs.Vj if rs.Vj is not None else 0
                        offset = rs.Vk if rs.Vk is not None else inst_obj.address # Usar Vk se for offset numérico, senão address da instrução
                        effective_address = base_val + offset
                        result = self.memory[effective_address] # Lê da memória
                    elif inst_obj.opname in ['SW', 'SB']:
                        # Vj = valor do registrador base (Rbase)
                        # Vk = valor do registrador fonte (Rsrc, valor a ser armazenado)
                        # offset = inst_obj.address
                        base_reg_value = rs.Vj 
                        value_to_be_stored = rs.Vk 
                        offset = inst_obj.address 

                        effective_address = base_reg_value + offset
                        self.memory[effective_address] = value_to_be_stored # Escreve na memória
                        result = "MEM_STORED" # Indica que a operação de escrita foi feita
                    elif inst_obj.opname in ['BEQ', 'BNE']:
                        val1 = rs.Vj if rs.Vj is not None else 0
                        val2 = rs.Vk if rs.Vk is not None else 0
                        
                        condition_met = (val1 == val2 if inst_obj.opname == 'BEQ' else val1 != val2)
                        rob_entry.actual_taken = PREDICT_TAKEN if condition_met else PREDICT_NOT_TAKEN # Registra resultado real
                        result = "BRANCH_EVALUATED"
                    
                    rob_entry.value = result # Armazena o resultado no ROB

    # Estágio de Escrita de Resultado (Write Result - CDB)
    def write_result_stage(self):
        # Coleta ROBs que terminaram execução e estão prontos para escrever
        ready_to_write_robs = sorted([
            rob for rob in self.reorder_buffer 
            if rob.busy and rob.state == "Ready to Write" and rob.instruction.write_result_cycle == -1
        ], key=lambda x: x.id)

        if ready_to_write_robs:
            rob_entry_to_broadcast = ready_to_write_robs[0] # Pega o mais antigo pronto
            
            rob_id_to_broadcast = rob_entry_to_broadcast.id
            result_value = rob_entry_to_broadcast.value
            inst_obj = rob_entry_to_broadcast.instruction
            
            inst_obj.write_result_cycle = self.current_cycle
            rob_entry_to_broadcast.state = "Write Result"

            # Atualiza todas as RSs esperando por este resultado (via Qj/Qk)
            for rs in self.reservation_stations:
                if rs.busy:
                    if rs.Qj == rob_id_to_broadcast:
                        rs.Vj = result_value
                        rs.Qj = None
                    if rs.Qk == rob_id_to_broadcast:
                        rs.Vk = result_value
                        rs.Qk = None
            
            # Limpa a RS que produziu o resultado
            for rs in self.reservation_stations:
                if rs.destination_rob_id == rob_id_to_broadcast and rs.busy:
                    rs.clear()
                    break 

    # Estágio de Confirmação (Commit)
    def commit_stage(self):
        committed_this_cycle = False
        head_rob_entry = self.reorder_buffer[self.rob_head]

        # Instrução pode confirmar se está na cabeça do ROB e em "Write Result"
        if head_rob_entry.busy and head_rob_entry.state == "Write Result":
            inst_obj = head_rob_entry.instruction

            if head_rob_entry.is_branch:
                predicted = head_rob_entry.predicted_taken
                actual = head_rob_entry.actual_taken

                if predicted != actual: # Previsão de desvio incorreta (misprediction)
                    print(f"!!! Misprediction de Branch em ROB ID {head_rob_entry.id} (Inst: {inst_obj})!")
                    print(f"Previsto: {predicted}, Real: {actual}. Limpando pipeline.")
                    
                    # Corrige PC para o endereço correto (real)
                    if actual == PREDICT_TAKEN:
                        self.program_counter = inst_obj.address
                    else:
                        self.program_counter = self.program_instructions.index(inst_obj) + 1
                    
                    # Limpa instruções especulativas no ROB e RSs
                    temp_rob_id = (self.rob_head + 1) % len(self.reorder_buffer)
                    while temp_rob_id != self.rob_tail:
                        rob_to_flush = self.reorder_buffer[temp_rob_id]
                        if rob_to_flush.busy:
                            if rob_to_flush.destination_reg:
                                reg = self.register_file.get(rob_to_flush.destination_reg)
                                if reg and reg.reorder_tag == rob_to_flush.id: # Se esta é a última a renomear
                                    reg.clear() 
                            rob_to_flush.clear() 
                        temp_rob_id = (temp_rob_id + 1) % len(self.reorder_buffer)
                    
                    self.rob_tail = (self.rob_head + 1) % len(self.reorder_buffer) # ROB tail volta após o branch
                    
                    for rs in self.reservation_stations: # Limpa todas as RSs
                        if rs.busy:
                            rs.clear()
                    
                    self.bubble_cycles += 1 # Conta penalidade de bolha
                
                # Confirma a instrução de branch (limpa entrada do ROB)
                inst_obj.commit_cycle = self.current_cycle
                head_rob_entry.state = "Commit"
                head_rob_entry.busy = False
                head_rob_entry.clear()
                self.rob_head = (self.rob_head + 1) % len(self.reorder_buffer)
                self.committed_instructions_count += 1
                committed_this_cycle = True

            elif head_rob_entry.inst_type == "STORE":
                # Para STORE, a escrita na memória já ocorreu; apenas confirma a instrução
                inst_obj.commit_cycle = self.current_cycle
                head_rob_entry.state = "Commit"
                head_rob_entry.busy = False
                head_rob_entry.clear()
                self.rob_head = (self.rob_head + 1) % len(self.reorder_buffer)
                self.committed_instructions_count += 1
                committed_this_cycle = True

            else: # Instrução ALU ou LOAD (escreve em registrador)
                dest_reg_name = head_rob_entry.destination_reg
                if dest_reg_name:
                    reg = self.register_file[dest_reg_name]
                    # Atualiza registrador físico e limpa "busy" SE esta entrada do ROB ainda for a mais recente a renomeá-lo
                    if reg.reorder_tag == head_rob_entry.id:
                        reg.value = head_rob_entry.value 
                        reg.clear() 
                inst_obj.commit_cycle = self.current_cycle
                head_rob_entry.state = "Commit"
                head_rob_entry.busy = False
                head_rob_entry.clear()
                self.rob_head = (self.rob_head + 1) % len(self.reorder_buffer)
                self.committed_instructions_count += 1
                committed_this_cycle = True
        return committed_this_cycle

    # Avança o simulador em um ciclo de clock, executando todos os estágios
    def clock_tick(self):
        self.current_cycle += 1

        # Ordem de execução dos estágios para simulação de pipeline
        committed = self.commit_stage()
        self.write_result_stage()
        self.execute_stage()
        issued = self.issue_stage()
        
        # Determina se foi um ciclo de bolha
        if not issued and not committed and not self.is_finished():
            self.bubble_cycles += 1

    # Verifica se a simulação terminou (todas as instruções emitidas e confirmadas)
    def is_finished(self):
        is_all_issued = (self.program_counter >= len(self.program_instructions))
        is_rob_empty = all(not entry.busy for entry in self.reorder_buffer)
        return is_all_issued and is_rob_empty

    # Calcula e retorna as métricas de desempenho
    def get_metrics(self):
        total_cycles = self.current_cycle
        ipc = self.committed_instructions_count / total_cycles if total_cycles > 0 else 0
        return {
            "Total Cycles": total_cycles,
            "Committed Instructions": self.committed_instructions_count,
            "IPC": ipc,
            "Bubble Cycles": self.bubble_cycles
        }

    # Reseta o simulador para o estado inicial
    def reset_simulator(self):
        self.register_file = {} # Limpa registradores
        self.memory = collections.defaultdict(int) # Limpa memória
        self.instruction_queue = collections.deque() # Limpa fila de instruções (não usada diretamente)
        self.program_counter = 0 # PC volta para 0

        for rs in self.reservation_stations: rs.clear() # Limpa todas as RSs
        for rob_pos in self.reorder_buffer: rob_pos.clear() # Limpa todas as entradas do ROB
        
        self.rob_head = 0
        self.rob_tail = 0

        self.current_cycle = 0
        self.committed_instructions_count = 0
        self.bubble_cycles = 0
        self.is_running = False
        
        # load_instructions será chamado pela lógica da GUI ao resetar para recarregar o programa

# --- Classe TomasuloGUI (Interface Gráfica do Usuário) ---
class TomasuloGUI:
    def __init__(self, master, simulator):
        self.master = master
        self.master.title("Simulador Tomasulo")
        self.simulator = simulator
        self.running_auto = False # Flag para modo "Executar Tudo"

        self._create_dummy_instructions_file() # Garante que instructions.txt exista para carga inicial

        self.setup_ui() # Configura todos os elementos da interface
        self.load_initial_program() # Carrega o programa na inicialização

    # Cria ou sobrescreve instructions.txt com um programa de exemplo
    def _create_dummy_instructions_file(self):
        filename = "instructions.txt"
        # Verifica se o arquivo existe e se não está vazio
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            print(f"Arquivo '{filename}' já existe e não está vazio. Usando conteúdo existente.")
            return # Não escreve o exemplo, usa o que já está no arquivo

        # Se o arquivo não existe ou está vazio, escreve o conteúdo padrão
        print(f"Arquivo '{filename}' não encontrado ou vazio. Criando com instruções de exemplo.")
        with open(filename, "w") as f:
            f.write("""
LW R6, R12, 8
LW R2, R13, 11
OR R0, R2, R4
SUB R8, R2, R6
AND R10, R0, R6
ADD R6, R8, R2
BEQ R6, R0, 10
ADD R14, R12, R13
SUB R15, R14, R6
OR R16, R15, R0
AND R17, R16, R2
ADD R18, R17, R4
SW R18, R0, 16
LW R19, R0, 12
""")

    # Configura a interface do usuário
    def setup_ui(self):
        self.master.grid_rowconfigure(0, weight=1)
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_columnconfigure(1, weight=1)

        # Frame Esquerdo: Programa, Controles, Métricas
        left_frame = ttk.Frame(self.master, padding="10")
        left_frame.grid(row=0, column=0, sticky="nsew")
        left_frame.grid_rowconfigure(0, weight=1) # Linha do programa
        left_frame.grid_rowconfigure(1, weight=0) # Linha dos controles
        left_frame.grid_rowconfigure(2, weight=0) # Linha das métricas
        left_frame.grid_columnconfigure(0, weight=1)

        # Frame Direito: ROB, RS, Registradores, Memória
        right_frame = ttk.Frame(self.master, padding="10")
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.grid_rowconfigure(0, weight=1)
        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_rowconfigure(2, weight=1)
        right_frame.grid_rowconfigure(3, weight=1) # Espaço extra (future use or padding)
        for i in range(4):
            right_frame.grid_columnconfigure(i, weight=1)

        # --- Exibição do Programa (Frame Esquerdo) ---
        ttk.Label(left_frame, text="Programa de Instruções:").grid(row=0, column=0, sticky="nw", pady=(0, 5))
        self.program_text = scrolledtext.ScrolledText(left_frame, wrap=tk.WORD, height=15, width=40, state='disabled')
        self.program_text.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))

        # --- Controles (Frame Esquerdo) ---
        control_frame = ttk.Frame(left_frame)
        control_frame.grid(row=1, column=0, sticky="ew", pady=(10, 5))
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)
        control_frame.columnconfigure(2, weight=1)
        control_frame.columnconfigure(3, weight=1)

        self.next_cycle_button = ttk.Button(control_frame, text="Próximo Ciclo", command=self.next_cycle)
        self.next_cycle_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        self.run_all_button = ttk.Button(control_frame, text="Executar Tudo", command=self.run_all)
        self.run_all_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        self.reset_button = ttk.Button(control_frame, text="Reiniciar", command=self.reset_simulation)
        self.reset_button.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        self.load_program_button = ttk.Button(control_frame, text="Carregar Programa", command=self.load_initial_program)
        self.load_program_button.grid(row=0, column=3, padx=5, pady=5, sticky="ew")

        # --- Exibição de Métricas (Frame Esquerdo) ---
        metrics_frame = ttk.LabelFrame(left_frame, text="Métricas de Desempenho", padding="10")
        metrics_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self.metrics_labels = {}
        metrics_order = ["Total Cycles", "Committed Instructions", "IPC", "Bubble Cycles", "Program Counter (PC)", "ROB Head", "ROB Tail"]
        for i, metric in enumerate(metrics_order):
            ttk.Label(metrics_frame, text=f"{metric}:").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            value_label = ttk.Label(metrics_frame, text="0")
            value_label.grid(row=i, column=1, sticky="w", padx=5, pady=2)
            self.metrics_labels[metric] = value_label

        # --- Tabela do ROB (Frame Direito) ---
        ttk.Label(right_frame, text="Buffer de Reordenação (ROB):").grid(row=0, column=0, sticky="nw", pady=(0, 5), columnspan=4)
        self.rob_tree = self._create_treeview(right_frame, 
            ["ID", "Ocupado", "Instrução", "Estado", "Reg. Dest.", "Valor", "Tipo", "Previsto", "Real"],
            {"ID": 40, "Ocupado": 60, "Instrução": 150, "Estado": 100, "Reg. Dest.": 80, "Valor": 80, "Tipo": 60, "Previsto": 60, "Real": 60}
        )
        self.rob_tree.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(25, 10))

        # --- Tabela das RSs (Frame Direito) ---
        ttk.Label(right_frame, text="Estações de Reserva (RS):").grid(row=1, column=0, sticky="nw", pady=(0, 5), columnspan=4)
        self.rs_tree = self._create_treeview(right_frame,
            ["Nome", "Ocupado", "Op", "Vj", "Vk", "Qj", "Qk", "ROB Dest."],
            {"Nome": 60, "Ocupado": 60, "Op": 50, "Vj": 70, "Vk": 70, "Qj": 50, "Qk": 50, "ROB Dest.": 80}
        )
        self.rs_tree.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(25, 10))

        # --- Tabela do Arquivo de Registradores (Frame Direito) ---
        ttk.Label(right_frame, text="Arquivo de Registradores:").grid(row=2, column=0, sticky="nw", pady=(0, 5), columnspan=2)
        self.reg_tree = self._create_treeview(right_frame,
            ["Registrador", "Valor", "Tag ROB", "Ocupado"],
            {"Registrador": 80, "Valor": 80, "Tag ROB": 70, "Ocupado": 60}
        )
        self.reg_tree.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(25, 10), padx=(0, 5))

        # --- Tabela da Memória (Frame Direito) ---
        ttk.Label(right_frame, text="Memória:").grid(row=2, column=2, sticky="nw", pady=(0, 5), columnspan=2)
        self.mem_tree = self._create_treeview(right_frame,
            ["Endereço", "Valor"],
            {"Endereço": 80, "Valor": 80}
        )
        self.mem_tree.grid(row=2, column=2, columnspan=2, sticky="nsew", pady=(25, 10), padx=(5, 0))

        self.update_gui() # Atualização inicial para exibir o estado vazio/programa carregado

    # Cria uma Treeview (tabela) com colunas e larguras definidas
    def _create_treeview(self, parent_frame, columns, widths):
        tree = ttk.Treeview(parent_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=widths.get(col, 100), anchor="center") # Largura padrão 100
        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        # Posicionamento da scrollbar, importante para que ela apareça
        tree.grid_configure(sticky="nsew") # Treeview deve se expandir
        vsb.grid(row=tree.grid_info()['row'], column=tree.grid_info()['column']+len(columns)-1, 
                 rowspan=tree.grid_info()['rowspan'], sticky='ns') # Posiciona scrollbar

        return tree

    # Carrega o programa inicial e inicializa valores
    def load_initial_program(self):
        self.simulator.reset_simulator() # Reseta o estado do simulador
        if self.simulator.load_instructions():
            self.program_text.config(state='normal')
            self.program_text.delete(1.0, tk.END)
            for idx, inst in enumerate(self.simulator.program_instructions):
                self.program_text.insert(tk.END, f"[{idx}]: {inst}\n")
            self.program_text.config(state='disabled')
            self.initial_program_loaded = True
            messagebox.showinfo("Sucesso", "Programa de instruções carregado com sucesso!")
        else:
            self.initial_program_loaded = False
        
        # Inicializa valores de registradores e memória para o programa de exemplo
        # Esta parte pode ser configurada pelo usuário ou lida de um arquivo.
        if 'R0' not in self.simulator.register_file: self.simulator.register_file['R0'] = Register('R0')
        self.simulator.register_file['R0'].value = 0 # Convenção MIPS
        if 'R4' not in self.simulator.register_file: self.simulator.register_file['R4'] = Register('R4')
        self.simulator.register_file['R4'].value = 1
        if 'R12' not in self.simulator.register_file: self.simulator.register_file['R12'] = Register('R12')
        self.simulator.register_file['R12'].value = 100 # Endereço base
        if 'R13' not in self.simulator.register_file: self.simulator.register_file['R13'] = Register('R13')
        self.simulator.register_file['R13'].value = 200 # Endereço base

        self.simulator.memory[108] = 500 # Para LW R6, R12, 8 (100 + 8 = 108)
        self.simulator.memory[211] = 600 # Para LW R2, R13, 11 (200 + 11 = 211)
        self.simulator.memory[16] = 0 # Valor inicial para Mem[16] - será sobrescrito por SW
        self.simulator.memory[12] = 777 # Valor inicial para Mem[12] para LW R19, R0, 12

        self.update_gui() # Atualiza a GUI com os valores iniciais

    # Executa o próximo ciclo da simulação
    def next_cycle(self):
        if not self.initial_program_loaded:
            messagebox.showwarning("Aviso", "Por favor, carregue um programa primeiro.")
            return

        if not self.simulator.is_finished():
            self.simulator.clock_tick()
            self.update_gui()
            if self.simulator.is_finished():
                messagebox.showinfo("Simulação Concluída", "Todas as instruções foram processadas!")
                self.running_auto = False 
        else:
            messagebox.showinfo("Simulação Concluída", "Todas as instruções já foram processadas!")
            self.running_auto = False

    # Inicia a execução contínua da simulação
    def run_all(self):
        if not self.initial_program_loaded:
            messagebox.showwarning("Aviso", "Por favor, carregue um programa primeiro.")
            return
        
        self.running_auto = True
        self._run_all_cycles()

    # Loop para execução contínua com atraso
    def _run_all_cycles(self):
        if self.running_auto and not self.simulator.is_finished():
            self.simulator.clock_tick()
            self.update_gui()
            self.master.after(100, self._run_all_cycles) # Agenda o próximo ciclo após 100ms
        elif self.simulator.is_finished():
            messagebox.showinfo("Simulação Concluída", "Todas as instruções foram processadas!")
            self.running_auto = False

    # Reseta a simulação
    def reset_simulation(self):
        self.running_auto = False # Para qualquer execução automática
        self.simulator.reset_simulator()
        self.load_initial_program() # Recarrega instruções e reinicializa o estado da GUI
        messagebox.showinfo("Reiniciar", "Simulação reiniciada.")

    # Atualiza todos os componentes da GUI com o estado atual do simulador
    def update_gui(self):
        # Atualiza tabela do ROB
        for i in self.rob_tree.get_children():
            self.rob_tree.delete(i)
        for entry in self.simulator.reorder_buffer:
            self.rob_tree.insert("", "end", values=(
                entry.id,
                "Sim" if entry.busy else "Não",
                str(entry.instruction) if entry.instruction else "",
                entry.state,
                str(entry.destination_reg) if entry.destination_reg else "",
                str(entry.value) if entry.value is not None else "",
                entry.inst_type,
                "T" if entry.predicted_taken == PREDICT_TAKEN else ("NT" if entry.predicted_taken == PREDICT_NOT_TAKEN else ""),
                "T" if entry.actual_taken == PREDICT_TAKEN else ("NT" if entry.actual_taken == PREDICT_NOT_TAKEN else "")
            ))
        
        # Atualiza tabela das RSs
        for i in self.rs_tree.get_children():
            self.rs_tree.delete(i)
        for rs in self.simulator.reservation_stations:
            self.rs_tree.insert("", "end", values=(
                rs.name,
                "Sim" if rs.busy else "Não",
                str(rs.op) if rs.op else "",
                str(rs.Vj) if rs.Vj is not None else "",
                str(rs.Vk) if rs.Vk is not None else "",
                str(rs.Qj) if rs.Qj is not None else "",
                str(rs.Qk) if rs.Qk is not None else "",
                str(rs.destination_rob_id) if rs.destination_rob_id is not None else ""
            ))

        # Atualiza tabela do Arquivo de Registradores
        for i in self.reg_tree.get_children():
            self.reg_tree.delete(i)
        sorted_regs = sorted(self.simulator.register_file.values(), key=lambda r: r.name)
        for reg in sorted_regs:
            self.reg_tree.insert("", "end", values=(
                reg.name,
                str(reg.value),
                str(reg.reorder_tag) if reg.reorder_tag is not None else "",
                "Sim" if reg.busy else "Não"
            ))

        # Atualiza tabela da Memória (primeiras 20 posições para simplicidade)
        for i in self.mem_tree.get_children():
            self.mem_tree.delete(i)
        for i in range(20): 
            self.mem_tree.insert("", "end", values=(f"End. {i}", self.simulator.memory[i]))
        
        # Atualiza métricas
        metrics = self.simulator.get_metrics()
        self.metrics_labels["Total Cycles"].config(text=str(metrics["Total Cycles"]))
        self.metrics_labels["Committed Instructions"].config(text=str(metrics["Committed Instructions"]))
        self.metrics_labels["IPC"].config(text=f"{metrics['IPC']:.2f}")
        self.metrics_labels["Bubble Cycles"].config(text=str(metrics["Bubble Cycles"]))
        self.metrics_labels["Program Counter (PC)"].config(text=str(self.simulator.program_counter))
        self.metrics_labels["ROB Head"].config(text=str(self.simulator.rob_head))
        self.metrics_labels["ROB Tail"].config(text=str(self.simulator.rob_tail))

        # Destaca a instrução atual na área de texto do programa
        self.program_text.config(state='normal')
        self.program_text.tag_remove("highlight", "1.0", tk.END)
        if self.simulator.program_counter < len(self.simulator.program_instructions):
            line_number = self.simulator.program_counter + 1 # Linhas no Text widget são 1-indexadas
            self.program_text.tag_add("highlight", f"{line_number}.0", f"{line_number}.end")
            self.program_text.tag_config("highlight", background="yellow")
        self.program_text.config(state='disabled')


# --- Ponto de Entrada Principal da Aplicação ---
if __name__ == "__main__":
    root = tk.Tk() # Cria a janela principal do Tkinter
    simulator_instance = TomasuloSimulator() # Cria uma instância do simulador
    gui = TomasuloGUI(root, simulator_instance) # Cria e exibe a GUI
    root.mainloop() # Inicia o loop de eventos do Tkinter
