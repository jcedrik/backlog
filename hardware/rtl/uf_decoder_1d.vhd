--------------------------------------------------------------------------------
-- uf_decoder_1d: Union-Find decoder for a 1D chain (repetition code), in RTL.
--
-- One growth round per clock cycle. All cluster bookkeeping (segment parity,
-- boundary contact, oddness) is combinational over the current grown-edge
-- vector, so there is no pointer chasing and no memory: just registers and
-- prefix/suffix XOR chains. Peeling is also combinational and is registered
-- in the cycle where no odd cluster remains.
--
-- Latency = number of growth rounds (reported on `rounds`) + 1 cycle to latch
-- the inputs. The golden model is hardware/model/uf1d_model.py and the RTL
-- must match it bit for bit (see hardware/tb/tb_uf_decoder_1d.vhd).
--
-- Layout (N detectors, N+1 data qubits, code distance d = N + 1):
--   edge 0     : left boundary -- D0
--   edge k     : D(k-1) -- D(k)       1 <= k <= N-1
--   edge N     : D(N-1) -- right boundary
--   obs_flip   : predicted flip of data qubit 0 (= correction(0))
--------------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity uf_decoder_1d is
  generic (
    N : positive := 8  -- number of detectors
  );
  port (
    clk        : in  std_logic;
    rst        : in  std_logic;                     -- synchronous, active high
    start      : in  std_logic;                     -- 1-cycle pulse when idle
    syndrome   : in  std_logic_vector(N-1 downto 0);
    busy       : out std_logic;
    done       : out std_logic;                     -- 1-cycle pulse, outputs valid
    correction : out std_logic_vector(N downto 0);  -- one bit per data qubit
    obs_flip   : out std_logic;
    rounds     : out unsigned(7 downto 0)           -- growth rounds used
  );
end entity uf_decoder_1d;

architecture rtl of uf_decoder_1d is

  type state_t is (S_IDLE, S_GROW);
  signal state : state_t := S_IDLE;

  signal defect_r : std_logic_vector(N-1 downto 0) := (others => '0');
  signal grown_r  : std_logic_vector(N downto 0)   := (others => '0');
  signal half_r   : std_logic_vector(N downto 0)   := (others => '0');
  signal rounds_r : unsigned(7 downto 0)           := (others => '0');

  signal corr_r : std_logic_vector(N downto 0) := (others => '0');
  signal obs_r  : std_logic := '0';
  signal done_r : std_logic := '0';

  -- combinational results
  signal any_odd_c : std_logic;
  signal grown_n_c : std_logic_vector(N downto 0);
  signal half_n_c  : std_logic_vector(N downto 0);
  signal corr_c    : std_logic_vector(N downto 0);

begin

  ------------------------------------------------------------------------------
  -- Combinational cluster info, growth step and peeling
  ------------------------------------------------------------------------------
  comb : process (all)
    variable L, R, BL, BR, odd : std_logic_vector(N-1 downto 0);
    variable inc               : natural range 0 to 2;
    variable g                 : natural range 0 to 3;
    variable anyo              : std_logic;
  begin
    -- prefix within segment (left to right)
    for v in 0 to N-1 loop
      if v = 0 then
        L(v)  := defect_r(v);
        BL(v) := grown_r(0);
      elsif grown_r(v) = '1' then
        L(v)  := defect_r(v) xor L(v-1);
        BL(v) := BL(v-1);
      else
        L(v)  := defect_r(v);
        BL(v) := '0';
      end if;
    end loop;

    -- suffix within segment (right to left)
    for v in N-1 downto 0 loop
      if v = N-1 then
        R(v)  := defect_r(v);
        BR(v) := grown_r(N);
      elsif grown_r(v+1) = '1' then
        R(v)  := defect_r(v) xor R(v+1);
        BR(v) := BR(v+1);
      else
        R(v)  := defect_r(v);
        BR(v) := '0';
      end if;
    end loop;

    -- a cluster is odd if its parity is 1 and it touches no boundary
    anyo := '0';
    for v in 0 to N-1 loop
      odd(v) := (L(v) xor R(v) xor defect_r(v)) and not (BL(v) or BR(v));
      anyo   := anyo or odd(v);
    end loop;
    any_odd_c <= anyo;

    -- growth: each odd neighbour adds one half-edge
    for k in 0 to N loop
      -- nested ifs so no out-of-range index is ever formed at k = 0 or k = N
      inc := 0;
      if k >= 1 then
        if odd(k-1) = '1' then
          inc := inc + 1;
        end if;
      end if;
      if k <= N-1 then
        if odd(k) = '1' then
          inc := inc + 1;
        end if;
      end if;
      if grown_r(k) = '1' then
        grown_n_c(k) <= '1';
        half_n_c(k)  <= '0';
      else
        if half_r(k) = '1' then
          g := 1 + inc;
        else
          g := inc;
        end if;
        if g >= 2 then grown_n_c(k) <= '1'; else grown_n_c(k) <= '0'; end if;
        if g = 1  then half_n_c(k)  <= '1'; else half_n_c(k)  <= '0'; end if;
      end if;
    end loop;

    -- peeling: proven valid in hardware/README.md
    for k in 0 to N loop
      if grown_r(k) = '0' then
        corr_c(k) <= '0';
      elsif k = 0 then
        corr_c(k) <= R(0);
      elsif k = N then
        if BL(N-1) = '1' then
          corr_c(k) <= '0';
        else
          corr_c(k) <= L(N-1);
        end if;
      else
        if BL(k-1) = '1' then
          corr_c(k) <= R(k);
        else
          corr_c(k) <= L(k-1);
        end if;
      end if;
    end loop;
  end process comb;

  ------------------------------------------------------------------------------
  -- Sequential control
  ------------------------------------------------------------------------------
  seq : process (clk)
  begin
    if rising_edge(clk) then
      done_r <= '0';
      if rst = '1' then
        state    <= S_IDLE;
        defect_r <= (others => '0');
        grown_r  <= (others => '0');
        half_r   <= (others => '0');
        rounds_r <= (others => '0');
        corr_r   <= (others => '0');
        obs_r    <= '0';
      else
        case state is
          when S_IDLE =>
            if start = '1' then
              defect_r <= syndrome;
              grown_r  <= (others => '0');
              half_r   <= (others => '0');
              rounds_r <= (others => '0');
              state    <= S_GROW;
            end if;

          when S_GROW =>
            if any_odd_c = '0' then
              -- every cluster is even: peel and publish
              corr_r <= corr_c;
              obs_r  <= corr_c(0);
              done_r <= '1';
              state  <= S_IDLE;
            else
              grown_r  <= grown_n_c;
              half_r   <= half_n_c;
              rounds_r <= rounds_r + 1;
            end if;
        end case;
      end if;
    end if;
  end process seq;

  busy       <= '1' when state = S_GROW else '0';
  done       <= done_r;
  correction <= corr_r;
  obs_flip   <= obs_r;
  rounds     <= rounds_r;

end architecture rtl;
